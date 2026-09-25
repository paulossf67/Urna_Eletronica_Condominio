"""
Camada de compromisso criptográfico e verificação pública dos votos.

Não é um zk-SNARK completo (exigiria circuitos e trusted setup).
Implementa propriedades no estilo zero-knowledge / verifiable voting:

1. COMPROMISSO (binding + hiding com nonce):
   compromisso = SHA256(tipo | candidato_id | peso | nonce)
   - Binding: não dá para mudar o voto depois sem mudar o hash
   - Hiding: sem o nonce, o hash não revela o conteúdo

2. RECIBO DO ELEITOR:
   O eleitor recebe o compromisso (e opcionalmente prova Merkle)
   Pode conferir depois se o voto entrou no quadro de boletins.

3. ÁRVORE DE MERKLE:
   Raiz pública de todos os compromissos da eleição.
   Prova de inclusão sem listar os outros votos.

4. VERIFICAÇÃO PÚBLICA DA APURAÇÃO:
   Decifra aberturas (autoridade), reconfere cada compromisso,
   recontagem independente e compara com o resultado oficial.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from typing import Any


def _canonico(tipo: str, candidato_id: int | None, peso: float, nonce: str, cargo: str = "") -> bytes:
    """Serialização canônica para o compromisso (ordem fixa, sem espaços)."""
    payload = {
        "tipo": tipo,
        "candidato_id": candidato_id,
        "cargo": cargo or "",
        "peso": round(float(peso), 6),
        "nonce": nonce,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def gerar_nonce() -> str:
    """Nonce aleatório de 128 bits (hex)."""
    return secrets.token_hex(16)


def criar_compromisso(
    tipo: str,
    candidato_id: int | None,
    peso: float,
    nonce: str | None = None,
    cargo: str = "",
) -> dict:
    """
    Cria compromisso de um voto.
    O cargo entra no compromisso para que um voto não possa ser transplantado
    de um cargo para outro sem quebrar o hash.
    Retorna dict com nonce, compromisso (hex) e abertura (para cifrar/armazenar).
    """
    if nonce is None:
        nonce = gerar_nonce()
    digest = hashlib.sha256(_canonico(tipo, candidato_id, peso, nonce, cargo)).hexdigest()
    return {
        "nonce": nonce,
        "compromisso": digest,
        "abertura": {
            "tipo": tipo,
            "candidato_id": candidato_id,
            "cargo": cargo or "",
            "peso": float(peso),
            "nonce": nonce,
        },
    }


def verificar_compromisso(
    compromisso: str,
    tipo: str,
    candidato_id: int | None,
    peso: float,
    nonce: str,
    cargo: str = "",
) -> bool:
    """Verifica se a abertura corresponde ao compromisso (binding)."""
    esperado = hashlib.sha256(_canonico(tipo, candidato_id, peso, nonce, cargo)).hexdigest()
    return hmac.compare_digest(esperado, compromisso)


# ---------------------------------------------------------------------------
# Merkle tree (SHA-256)
# ---------------------------------------------------------------------------

def _hash_folha(compromisso: str) -> bytes:
    return hashlib.sha256(b"voto:" + compromisso.encode("ascii")).digest()


def _hash_no(esq: bytes, dir_: bytes) -> bytes:
    return hashlib.sha256(b"no:" + esq + dir_).digest()


def construir_merkle(compromissos: list[str]) -> dict:
    """
    Constrói árvore de Merkle a partir da lista de compromissos (ordem estável).
    Retorna raiz (hex), níveis e folhas.
    """
    if not compromissos:
        raiz_vazia = hashlib.sha256(b"urna-vazia").hexdigest()
        return {"raiz": raiz_vazia, "folhas": [], "niveis": []}

    folhas = [_hash_folha(c) for c in compromissos]
    niveis = [folhas]
    atual = folhas
    while len(atual) > 1:
        prox = []
        for i in range(0, len(atual), 2):
            esq = atual[i]
            dir_ = atual[i + 1] if i + 1 < len(atual) else atual[i]
            prox.append(_hash_no(esq, dir_))
        niveis.append(prox)
        atual = prox

    return {
        "raiz": atual[0].hex(),
        "folhas": [f.hex() for f in folhas],
        "niveis": [[n.hex() for n in nivel] for nivel in niveis],
    }


def prova_merkle(compromissos: list[str], indice: int) -> dict | None:
    """Gera prova de inclusão Merkle para o compromisso no índice dado."""
    if indice < 0 or indice >= len(compromissos):
        return None

    arvore = construir_merkle(compromissos)
    # Reconstrói caminho
    folhas = [_hash_folha(c) for c in compromissos]
    caminho = []
    atual = folhas
    idx = indice
    while len(atual) > 1:
        if idx % 2 == 0:
            irmao_idx = idx + 1 if idx + 1 < len(atual) else idx
            lado = "dir"
        else:
            irmao_idx = idx - 1
            lado = "esq"
        caminho.append({"hash": atual[irmao_idx].hex(), "lado": lado})
        # sobe
        prox = []
        for i in range(0, len(atual), 2):
            esq = atual[i]
            dir_ = atual[i + 1] if i + 1 < len(atual) else atual[i]
            prox.append(_hash_no(esq, dir_))
        atual = prox
        idx //= 2

    return {
        "compromisso": compromissos[indice],
        "indice": indice,
        "caminho": caminho,
        "raiz": arvore["raiz"],
    }


def verificar_prova_merkle(compromisso: str, prova: dict) -> bool:
    """Verifica prova de inclusão Merkle."""
    h = _hash_folha(compromisso)
    for passo in prova.get("caminho", []):
        irmao = bytes.fromhex(passo["hash"])
        if passo["lado"] == "dir":
            h = _hash_no(h, irmao)
        else:
            h = _hash_no(irmao, h)
    return hmac.compare_digest(h.hex(), prova["raiz"])


# ---------------------------------------------------------------------------
# Verificação pública da apuração
# ---------------------------------------------------------------------------

def verificar_apuracao(
    aberturas: list[dict],
    compromissos: list[str],
    candidatos_validos: set[int],
) -> dict[str, Any]:
    """
    Verifica independentemente uma lista de aberturas contra compromissos.

    aberturas: lista de {tipo, candidato_id, peso, nonce}
    compromissos: lista paralela de hashes hex
    candidatos_validos: ids permitidos

    Retorna relatório de verificação e recontagem.
    """
    if len(aberturas) != len(compromissos):
        return {
            "ok": False,
            "erro": f"Quantidade diferente: {len(aberturas)} aberturas vs {len(compromissos)} compromissos",
        }

    erros = []
    contagem: dict[int, float] = {}
    brancos = nulos = 0
    peso_brancos = peso_nulos = 0.0
    peso_total = 0.0

    for i, (ab, comp) in enumerate(zip(aberturas, compromissos)):
        tipo = ab.get("tipo")
        cid = ab.get("candidato_id")
        peso = float(ab.get("peso") or 1.0)
        nonce = ab.get("nonce") or ""
        cargo = ab.get("cargo") or ""

        if not verificar_compromisso(comp, tipo, cid, peso, nonce, cargo):
            erros.append({"indice": i, "erro": "compromisso_invalido"})
            continue

        peso_total += peso
        if tipo == "candidato":
            if cid not in candidatos_validos:
                erros.append({"indice": i, "erro": "candidato_invalido", "candidato_id": cid})
                continue
            contagem[cid] = contagem.get(cid, 0.0) + peso
        elif tipo == "branco":
            brancos += 1
            peso_brancos += peso
        elif tipo == "nulo":
            nulos += 1
            peso_nulos += peso
        else:
            erros.append({"indice": i, "erro": "tipo_invalido", "tipo": tipo})

    merkle = construir_merkle(compromissos)

    return {
        "ok": len(erros) == 0,
        "total_votos": len(compromissos),
        "compromissos_validos": len(compromissos) - len(erros),
        "erros": erros,
        "contagem_por_candidato": contagem,
        "brancos": {"qtd": brancos, "peso": peso_brancos},
        "nulos": {"qtd": nulos, "peso": peso_nulos},
        "peso_total": peso_total,
        "merkle_raiz": merkle["raiz"],
    }
