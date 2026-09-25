"""
Criptografia dos votos — sigilo em repouso.

- Fernet (AES-128-CBC + HMAC) para cifrar o conteúdo do voto
- Chave mestra em arquivo urna.key (gerada na primeira execução)
- HMAC adicional para verificação de integridade

O voto cifrado NÃO contém identificação do eleitor.
Apenas: tipo (candidato/branco/nulo), id do candidato, peso.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

# Caminho da chave (mesmo diretório do banco, ou URNA_KEY)
_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_KEY = os.path.join(_DIR, "urna.key")
KEY_PATH = os.environ.get("URNA_KEY", _DEFAULT_KEY)

_fernet: Fernet | None = None
_hmac_key: bytes | None = None


def _carregar_ou_criar_chave() -> tuple[Fernet, bytes]:
    """Carrega a chave Fernet ou cria uma nova."""
    global _fernet, _hmac_key
    if _fernet is not None and _hmac_key is not None:
        return _fernet, _hmac_key

    if os.path.isfile(KEY_PATH):
        with open(KEY_PATH, "rb") as f:
            raw = f.read().strip()
        # Arquivo guarda: fernet_key (base64) + newline + hmac_secret (base64)
        partes = raw.split(b"\n")
        fernet_key = partes[0].strip()
        hmac_secret = base64.urlsafe_b64decode(partes[1].strip()) if len(partes) > 1 else hashlib.sha256(fernet_key).digest()
    else:
        fernet_key = Fernet.generate_key()
        hmac_secret = os.urandom(32)
        pasta = os.path.dirname(os.path.abspath(KEY_PATH))
        if pasta:
            os.makedirs(pasta, exist_ok=True)
        with open(KEY_PATH, "wb") as f:
            f.write(fernet_key + b"\n" + base64.urlsafe_b64encode(hmac_secret) + b"\n")
        try:
            os.chmod(KEY_PATH, 0o600)
        except OSError:
            pass

    _fernet = Fernet(fernet_key)
    _hmac_key = hmac_secret
    return _fernet, _hmac_key


def cifrar_voto(
    tipo_voto: str,
    candidato_id: int | None,
    peso: float,
    nonce: str | None = None,
    cargo: str = "",
) -> dict[str, str]:
    """
    Cria compromisso (SHA-256 + nonce) e cifra a abertura (Fernet + HMAC).
    Retorna: compromisso, voto_cifrado, integridade, nonce.
    """
    from zk_voto import criar_compromisso

    comp = criar_compromisso(tipo_voto, candidato_id, peso, nonce, cargo)
    fernet, hmac_key = _carregar_ou_criar_chave()
    claro = json.dumps(comp["abertura"], separators=(",", ":"), sort_keys=True).encode("utf-8")
    cifrado = fernet.encrypt(claro)
    tag = hmac.new(hmac_key, cifrado, hashlib.sha256).hexdigest()
    return {
        "compromisso": comp["compromisso"],
        "voto_cifrado": cifrado.decode("ascii"),
        "integridade": tag,
        "nonce": comp["nonce"],
    }


def decifrar_voto(payload_cifrado: str, tag_integridade: str | None = None) -> dict[str, Any]:
    """
    Decifra a abertura do voto. Se tag_integridade for informada, valida o HMAC.
    """
    fernet, hmac_key = _carregar_ou_criar_chave()
    cifrado = payload_cifrado.encode("ascii")

    if tag_integridade:
        esperado = hmac.new(hmac_key, cifrado, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(esperado, tag_integridade):
            raise ValueError("Integridade do voto comprometida (HMAC inválido)")

    try:
        claro = fernet.decrypt(cifrado)
    except InvalidToken as e:
        raise ValueError("Não foi possível decifrar o voto (chave incorreta ou dados corrompidos)") from e

    return json.loads(claro.decode("utf-8"))


def chave_existe() -> bool:
    return os.path.isfile(KEY_PATH)


def info_criptografia() -> dict:
    """Informações sobre o estado da criptografia (sem expor a chave)."""
    existe = chave_existe()
    info = {
        "ativo": True,
        "algoritmo": "Fernet (AES-128-CBC + HMAC-SHA256)",
        "compromisso": "SHA-256 + nonce (binding + hiding)",
        "merkle": "SHA-256 Merkle tree",
        "integridade": "HMAC-SHA256",
        "arquivo_chave": os.path.abspath(KEY_PATH),
        "chave_existe": existe,
    }
    if existe:
        info["tamanho_chave_bytes"] = os.path.getsize(KEY_PATH)
    return info
