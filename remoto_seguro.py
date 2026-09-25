"""
Votação remota segura
=====================

Camadas:
  1. Desafio (challenge) — anti-replay na autenticação
  2. Sessão HMAC de curta duração — autoriza um único voto
  3. Nonce de voto — impede reenvio da mesma requisição
  4. Rate limit por CPF — trava tentativa em massa

Tudo é persistido no próprio SQLite: um reinício do servidor não derruba
eleitores no meio da votação, e o anti-replay não depende de memória RAM
(que antes era esvaziada ao encher, reabrindo a janela de replay).

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

from database import (
    db_session, bloqueado_por_tentativas, registrar_tentativa, logger,
)

# Segredo do servidor para assinar tokens (persistido)
_SECRET_PATH = os.environ.get(
    "URNA_REMOTO_SECRET",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "urna_remoto.secret"),
)

TTL_DESAFIO = 120        # segundos
TTL_SESSAO = 300         # 5 minutos para concluir o voto
MAX_TENTATIVAS = 5
RETENCAO_NONCE = 7 * 24 * 3600   # nonces ficam 7 dias; nunca são apagados em bloco


def _carregar_segredo() -> bytes:
    if os.path.isfile(_SECRET_PATH):
        with open(_SECRET_PATH, "rb") as f:
            seg = f.read().strip()
        if len(seg) >= 32:
            return seg
        logger.warning("Segredo remoto muito curto em %s — gerando um novo.", _SECRET_PATH)
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
    """Remove desafios e sessões vencidos e nonces além da janela de retenção."""
    agora = time.time()
    with db_session() as conn:
        conn.execute("DELETE FROM desafios_remotos WHERE expira_em < ?", (agora,))
        conn.execute("DELETE FROM sessoes_remotas WHERE expira_em < ?", (agora,))
        # Um nonce só some depois da retenção — e nunca antes da sessão
        # correspondente expirar, então não há como reaproveitá-lo.
        conn.execute(
            "DELETE FROM nonces_remotos WHERE registrado_em < ?",
            (agora - RETENCAO_NONCE,),
        )


# ---------------------------------------------------------------------------
# 1. Desafio de autenticação
# ---------------------------------------------------------------------------

def criar_desafio(eleicao_id: int) -> dict:
    """Cliente pede desafio antes de enviar CPF."""
    _limpar_expirados()
    cid = secrets.token_hex(16)
    with db_session() as conn:
        conn.execute(
            "INSERT INTO desafios_remotos (challenge_id, eleicao_id, expira_em) VALUES (?, ?, ?)",
            (cid, eleicao_id, time.time() + TTL_DESAFIO),
        )
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
    Valida o desafio e emite um token de sessão de uso único para votar.
    Aplica rate limit por CPF antes de consumir o desafio.
    """
    _limpar_expirados()

    espera = bloqueado_por_tentativas("remoto", cpf_norm)
    if espera > 0:
        raise ValueError(f"Muitas tentativas. Aguarde {int(espera) + 1} segundos.")

    agora = time.time()
    # As tentativas são anotadas FORA da transação: abrir uma segunda
    # conexão com o BEGIN IMMEDIATE em curso travaria o próprio banco.
    try:
        with db_session(escrita=True) as conn:
            ch = conn.execute(
                "SELECT * FROM desafios_remotos WHERE challenge_id = ?", (challenge_id,)
            ).fetchone()

            if not ch:
                raise ValueError("Desafio inválido ou expirado")
            if ch["eleicao_id"] != eleicao_id:
                conn.execute(
                    "UPDATE desafios_remotos SET tentativas = tentativas + 1 WHERE challenge_id = ?",
                    (challenge_id,),
                )
                raise ValueError("Desafio não corresponde à eleição")
            if ch["expira_em"] < agora:
                conn.execute("DELETE FROM desafios_remotos WHERE challenge_id = ?", (challenge_id,))
                raise ValueError("Desafio expirado")
            if ch["tentativas"] + 1 > MAX_TENTATIVAS:
                conn.execute("DELETE FROM desafios_remotos WHERE challenge_id = ?", (challenge_id,))
                raise ValueError("Muitas tentativas neste desafio")

            # Consome o desafio — ele serve a uma única autenticação
            conn.execute("DELETE FROM desafios_remotos WHERE challenge_id = ?", (challenge_id,))

            token_id = secrets.token_hex(16)
            expira = int(agora) + TTL_SESSAO
            payload = {
                "tid": token_id,
                "eid": eleicao_id,
                "uid": eleitor_id,
                "cpf": cpf_norm,
                "exp": expira,
            }
            corpo = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            sig = hmac.new(_SEGREDO, corpo, hashlib.sha256).hexdigest()

            conn.execute(
                """INSERT INTO sessoes_remotas
                   (token_id, assinatura, eleicao_id, eleitor_id, cpf, expira_em, usado)
                   VALUES (?, ?, ?, ?, ?, ?, 0)""",
                (token_id, sig, eleicao_id, eleitor_id, cpf_norm, float(expira)),
            )
    except ValueError:
        registrar_tentativa("remoto", cpf_norm, False)
        raise

    registrar_tentativa("remoto", cpf_norm, True)
    return {
        "token": f"{token_id}.{sig}",
        "ttl": TTL_SESSAO,
        "eleitor_id": eleitor_id,
        "eleicao_id": eleicao_id,
    }


def validar_sessao(token: str, eleicao_id: int, eleitor_id: int) -> dict:
    """Valida token e garante que ainda não foi usado para votar."""
    _limpar_expirados()
    partes = (token or "").split(".")
    if len(partes) != 2:
        raise ValueError("Token malformado")
    token_id, sig = partes

    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM sessoes_remotas WHERE token_id = ?", (token_id,)
        ).fetchone()

    if not row:
        raise ValueError("Sessão inválida ou expirada")
    sess = dict(row)
    if sess["expira_em"] < time.time():
        raise ValueError("Sessão expirada")
    if not hmac.compare_digest(sess["assinatura"], sig):
        raise ValueError("Assinatura do token inválida")
    if sess["usado"]:
        raise ValueError("Sessão já utilizada (voto já registrado)")
    if sess["eleicao_id"] != eleicao_id or sess["eleitor_id"] != eleitor_id:
        raise ValueError("Sessão não autoriza este eleitor/eleição")
    return sess


def consumir_sessao(token: str, nonce: str, manter_ativa: bool = False) -> None:
    """
    Registra o nonce (anti-replay) e marca a sessão como usada.

    manter_ativa=True é usado quando a eleição tem vários cargos e o mesmo
    token autoriza um voto por cargo: cada requisição ainda precisa de um
    nonce novo, mas a sessão continua válida até o último cargo.
    """
    partes = (token or "").split(".")
    if len(partes) != 2:
        raise ValueError("Token malformado")
    token_id = partes[0]

    if not nonce or len(nonce) < 16:
        raise ValueError("Nonce obrigatório (mínimo 16 caracteres)")

    with db_session(escrita=True) as conn:
        sess = conn.execute(
            "SELECT token_id, usado, expira_em FROM sessoes_remotas WHERE token_id = ?",
            (token_id,),
        ).fetchone()
        if not sess:
            raise ValueError("Sessão inválida")
        if sess["usado"]:
            raise ValueError("Sessão já utilizada (voto já registrado)")
        if sess["expira_em"] < time.time():
            raise ValueError("Sessão expirada")

        # A PRIMARY KEY do nonce é o que bloqueia o replay, de forma atômica
        try:
            conn.execute(
                "INSERT INTO nonces_remotos (nonce, registrado_em) VALUES (?, ?)",
                (nonce, time.time()),
            )
        except Exception:
            raise ValueError("Nonce já utilizado (possível replay)")

        if not manter_ativa:
            conn.execute(
                "UPDATE sessoes_remotas SET usado = 1 WHERE token_id = ?", (token_id,)
            )


def encerrar_sessao(token: str) -> None:
    """Marca a sessão como consumida (fim da votação em todos os cargos)."""
    partes = (token or "").split(".")
    if len(partes) != 2:
        return
    with db_session() as conn:
        conn.execute(
            "UPDATE sessoes_remotas SET usado = 1 WHERE token_id = ?", (partes[0],)
        )


def gerar_nonce_voto() -> str:
    return secrets.token_hex(16)
