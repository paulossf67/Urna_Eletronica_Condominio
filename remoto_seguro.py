"""
Votação remota segura
=====================

Camadas:
  1. Desafio (challenge) — anti-replay na autenticação
  2. Sessão HMAC de curta duração — autoriza um único voto
  3. Nonce de voto — impede reenvio da mesma requisição
  4. OTP opcional — segundo fator além do CPF

NÃO substitui HTTPS/TLS na rede. Em produção:
  - Coloque o servidor atrás de Nginx/Caddy com TLS
  - Ou use VPN (WireGuard) entre cabines e servidor
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass, field

# Segredo do servidor para assinar tokens (persistido)
_SECRET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "urna_remoto.secret")
_SESSOES: dict[str, dict] = {}  # token_id -> dados (em memória; produção: Redis)
_DESAFIOS: dict[str, dict] = {}  # challenge_id -> dados
_NONCES_USADOS: set[str] = set()

TTL_DESAFIO = 120       # segundos
TTL_SESSAO = 300        # 5 minutos para concluir o voto
MAX_TENTATIVAS = 5


def _carregar_segredo() -> bytes:
    if os.path.isfile(_SECRET_PATH):
        with open(_SECRET_PATH, "rb") as f:
            return f.read().strip()
    seg = secrets.token_bytes(32)
    with open(_SECRET_PATH, "wb") as f:
        f.write(seg)
    try:
        os.chmod(_SECRET_PATH, 0o600)
    except OSError:
        pass
    return seg


_SEGREDO = _carregar_segredo()


def _limpar_expirados():
    agora = time.time()
    for d in (_DESAFIOS,):
        mortos = [k for k, v in _DESAFIOS.items() if v["expira"] < agora]
        for k in mortos:
            del _DESAFIOS[k]
    mortos = [k for k, v in _SESSOES.items() if v["expira"] < agora]
    for k in mortos:
        del _SESSOES[k]


# ---------------------------------------------------------------------------
# 1. Desafio de autenticação
# ---------------------------------------------------------------------------

def criar_desafio(eleicao_id: int) -> dict:
    """Cliente pede desafio antes de enviar CPF."""
    _limpar_expirados()
    cid = secrets.token_hex(16)
    _DESAFIOS[cid] = {
        "eleicao_id": eleicao_id,
        "expira": time.time() + TTL_DESAFIO,
        "tentativas": 0,
    }
    return {
        "challenge_id": cid,
        "ttl": TTL_DESAFIO,
        "mensagem": "Envie CPF + challenge_id na autenticação em até 2 minutos",
    }


def _hash_otp(otp: str) -> str:
    return hashlib.sha256(otp.encode("utf-8")).hexdigest()


def gerar_otp() -> str:
    """OTP numérico de 6 dígitos (entregar ao eleitor por canal separado)."""
    return f"{secrets.randbelow(10**6):06d}"


# ---------------------------------------------------------------------------
# 2. Sessão de votação (após autenticar CPF)
# ---------------------------------------------------------------------------

def criar_sessao(
    eleicao_id: int,
    eleitor_id: int,
    cpf_norm: str,
    challenge_id: str,
) -> dict:
    """
    Valida desafio e emite token de sessão de uso único para votar.
    """
    _limpar_expirados()
    ch = _DESAFIOS.get(challenge_id)
    if not ch:
        raise ValueError("Desafio inválido ou expirado")
    if ch["eleicao_id"] != eleicao_id:
        raise ValueError("Desafio não corresponde à eleição")
    if ch["expira"] < time.time():
        _DESAFIOS.pop(challenge_id, None)
        raise ValueError("Desafio expirado")
    ch["tentativas"] += 1
    if ch["tentativas"] > MAX_TENTATIVAS:
        _DESAFIOS.pop(challenge_id, None)
        raise ValueError("Muitas tentativas neste desafio")

    # Consome o desafio
    del _DESAFIOS[challenge_id]

    token_id = secrets.token_hex(16)
    expira = int(time.time()) + TTL_SESSAO
    payload = {
        "tid": token_id,
        "eid": eleicao_id,
        "uid": eleitor_id,
        "cpf": cpf_norm,
        "exp": expira,
        "usado": False,
    }
    # Assinatura HMAC
    corpo = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    sig = hmac.new(_SEGREDO, corpo, hashlib.sha256).hexdigest()
    token = f"{token_id}.{sig}"

    _SESSOES[token_id] = {
        **payload,
        "sig": sig,
        "expira": float(expira),
    }
    return {
        "token": token,
        "ttl": TTL_SESSAO,
        "eleitor_id": eleitor_id,
        "eleicao_id": eleicao_id,
    }


def validar_sessao(token: str, eleicao_id: int, eleitor_id: int) -> dict:
    """Valida token e garante que ainda não foi usado para votar."""
    _limpar_expirados()
    partes = token.split(".")
    if len(partes) != 2:
        raise ValueError("Token malformado")
    token_id, sig = partes
    sess = _SESSOES.get(token_id)
    if not sess:
        raise ValueError("Sessão inválida ou expirada")
    if sess["expira"] < time.time():
        _SESSOES.pop(token_id, None)
        raise ValueError("Sessão expirada")
    if sess["usado"]:
        raise ValueError("Sessão já utilizada (voto já registrado)")
    if sess["eid"] != eleicao_id or sess["uid"] != eleitor_id:
        raise ValueError("Sessão não autoriza este eleitor/eleição")
    if not hmac.compare_digest(sess["sig"], sig):
        raise ValueError("Assinatura do token inválida")
    return sess


def consumir_sessao(token: str, nonce: str) -> None:
    """Marca sessão como usada e registra nonce (anti-replay)."""
    if nonce in _NONCES_USADOS:
        raise ValueError("Nonce já utilizado (possível replay)")
    _NONCES_USADOS.add(nonce)
    # Limita memória de nonces
    if len(_NONCES_USADOS) > 50000:
        _NONCES_USADOS.clear()

    partes = token.split(".")
    if len(partes) != 2:
        raise ValueError("Token malformado")
    token_id = partes[0]
    sess = _SESSOES.get(token_id)
    if not sess:
        raise ValueError("Sessão inválida")
    sess["usado"] = True


def gerar_nonce_voto() -> str:
    return secrets.token_hex(16)
