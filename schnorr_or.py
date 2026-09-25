#!/usr/bin/env python3
"""
Provas OR de Schnorr (Cramer–Damgård–Schoenmakers)
===================================================

Objetivo didático + uso na urna:
  Provar que um voto corresponde a UM dos candidatos válidos,
  SEM revelar qual — propriedade no estilo zero-knowledge.

────────────────────────────────────────────────────────────
1. SCHNORR SIMPLES — prova de conhecimento de log discreto
────────────────────────────────────────────────────────────

  Grupo:  g gerador de ordem q em Z_p*
  Segredo: x  (o "voto codificado" ou chave)
  Público: y = g^x  mod p

  Prova interativa clássica:
    1. Prover escolhe r aleatório, envia t = g^r
    2. Verificador envia desafio e ∈ {0..q-1}
    3. Prover responde s = r + e·x  (mod q)
    4. Verificador checa: g^s ≟ t · y^e

  Fiat–Shamir (não interativo): e = H(g, y, t, contexto)

────────────────────────────────────────────────────────────
2. PROVA OR — "conheço o segredo de PELO MENOS UMA instância"
────────────────────────────────────────────────────────────

  Públicos: y_0, y_1, …, y_{n-1}
  Prover conhece x tal que y_j = g^x  para um único índice j (o voto real).

  Ideia CDS:
  • Para o índice REAL j: faz Schnorr honesto (com r real).
  • Para os índices FALSOS i≠j: simula a transcrição
    (escolhe s_i e e_i, calcula t_i = g^{s_i} / y_i^{e_i}).
  • Desafio global E = H(todos os t, contexto)
  • e_j = E − Σ_{i≠j} e_i  (mod q)   ← só o real "fecha" a conta
  • s_j = r + e_j · x  (mod q)

  O verificador vê n transcrições válidas e Σ e_i = E, mas não sabe
  qual foi a real (simuláveis são indistinguíveis).

────────────────────────────────────────────────────────────
3. APLICAÇÃO NA URNA
────────────────────────────────────────────────────────────

  Para cada candidato k com id público C_k:
    y_k = g^{H(C_k)} · h^{v}   (ou simplesmente y_k = g^{m_k} com m_k = id)

  Forma simplificada usada aqui (didática):
    • Cada candidato i tem y_i = g^{x_i} onde x_i deriva do id do candidato
    • O eleitor que vota no candidato j conhece x_j (pré-computado na urna)
    • Gera prova OR: "conheço o x de um dos y_i da lista oficial"

  Assim a urna (ou um auditor) verifica que o voto é em candidato válido
  sem ver qual, antes mesmo de abrir o compromisso na apuração.

Referência: Cramer, Damgård, Schoenmakers (CRYPTO 1994).
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from typing import Sequence


# ---------------------------------------------------------------------------
# Grupo Schnorr (multiplicativo) — parâmetros gerados de forma consistente
# p = 2q+1 (primo seguro), g gerador do subgrupo de ordem q.
# Tamanho de demo (~128 bits em q): suficiente para estudar o protocolo.
# Produção: use curva elíptica (secp256r1) ou p ≥ 2048 bits.
# ---------------------------------------------------------------------------

def _is_prime(n: int, rounds: int = 12) -> bool:
    if n < 2:
        return False
    small = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]
    for s in small:
        if n == s:
            return True
        if n % s == 0:
            return False
    # Miller–Rabin
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _gerar_grupo_schnorr(bits_q: int = 128) -> tuple[int, int, int]:
    """Retorna (p, q, g) com p=2q+1 primo, g de ordem q."""
    while True:
        q = secrets.randbits(bits_q) | 1
        # força bit alto
        q |= 1 << (bits_q - 1)
        if not _is_prime(q):
            continue
        p = 2 * q + 1
        if not _is_prime(p):
            continue
        # g = h^2 mod p tem ordem dividindo q (evita subgrupo {1,-1})
        for _ in range(100):
            h = secrets.randbelow(p - 3) + 2
            g = pow(h, 2, p)
            if g > 1 and pow(g, q, p) == 1:
                return p, q, g


import os as _os

_PARAMS_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "urna_schnorr.json")


def _carregar_ou_criar_params() -> tuple[int, int, int]:
    """Persiste (p,q,g) para que provas continuem verificáveis entre processos."""
    if _os.path.isfile(_PARAMS_PATH):
        with open(_PARAMS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return int(data["p"]), int(data["q"]), int(data["g"])
    p, q, g = _gerar_grupo_schnorr(128)
    with open(_PARAMS_PATH, "w", encoding="utf-8") as f:
        json.dump({"p": str(p), "q": str(q), "g": str(g)}, f)
    return p, q, g


P, Q, G = _carregar_ou_criar_params()


def mod_pow(base: int, exp: int, mod: int | None = None) -> int:
    return pow(base, exp, mod if mod is not None else P)


def mod_inv(a: int, mod: int | None = None) -> int:
    return pow(a, -1, mod if mod is not None else P)


def hash_desafio(*parts: bytes) -> int:
    """Fiat–Shamir: desafio em 0..Q-1."""
    h = hashlib.sha256()
    for p in parts:
        h.update(len(p).to_bytes(4, "big"))
        h.update(p)
    return int.from_bytes(h.digest(), "big") % Q


def int_to_bytes(x: int) -> bytes:
    bl = (x.bit_length() + 7) // 8 or 1
    return x.to_bytes(bl, "big")


def params_publicos() -> dict:
    """Parâmetros públicos do grupo (sem segredos)."""
    return {"p": str(P), "q": str(Q), "g": str(G), "arquivo": _PARAMS_PATH}


# ---------------------------------------------------------------------------
# Schnorr simples
# ---------------------------------------------------------------------------

@dataclass
class SchnorrProof:
    t: int  # compromisso g^r
    e: int  # desafio
    s: int  # resposta


def schnorr_provar(x: int, y: int | None = None, contexto: bytes = b"") -> tuple[int, SchnorrProof]:
    """
    Prova conhecimento de x tal que y = g^x.
    Se y não for passado, calcula y = g^x.
    """
    x = x % Q
    if y is None:
        y = mod_pow(G, x, P)
    r = secrets.randbelow(Q - 1) + 1
    t = mod_pow(G, r, P)
    e = hash_desafio(int_to_bytes(G), int_to_bytes(y), int_to_bytes(t), contexto)
    s = (r + e * x) % Q
    return y, SchnorrProof(t=t, e=e, s=s)


def schnorr_verificar(y: int, prova: SchnorrProof, contexto: bytes = b"") -> bool:
    """Verifica g^s = t * y^e  e que e = H(...)."""
    e2 = hash_desafio(int_to_bytes(G), int_to_bytes(y), int_to_bytes(prova.t), contexto)
    if e2 != prova.e % Q:
        return False
    esquerda = mod_pow(G, prova.s, P)
    direita = (prova.t * mod_pow(y, prova.e, P)) % P
    return esquerda == direita


# ---------------------------------------------------------------------------
# Prova OR (CDS)
# ---------------------------------------------------------------------------

@dataclass
class SchnorrORProof:
    """Prova de que o prover conhece o DL de pelo menos um y_i."""
    ts: list[int]       # t_0 .. t_{n-1}
    es: list[int]       # e_0 .. e_{n-1}
    ss: list[int]       # s_0 .. s_{n-1}
    # Verificador confere: Σ e_i = H(ts..., contexto) e cada g^{s_i} = t_i * y_i^{e_i}


def schnorr_or_provar(
    ys: Sequence[int],
    indice_real: int,
    x_real: int,
    contexto: bytes = b"urna-or",
) -> SchnorrORProof:
    """
    Prova OR: conhece x tal que ys[indice_real] = g^{x}.

    Para i ≠ indice_real: simula (escolhe e_i, s_i, deriva t_i).
    Para i = indice_real: Schnorr honesto com desafio residual.
    """
    n = len(ys)
    if n < 1:
        raise ValueError("Lista de y vazia")
    if not (0 <= indice_real < n):
        raise ValueError("indice_real inválido")

    x_real = x_real % Q
    ts = [0] * n
    es = [0] * n
    ss = [0] * n

    # Simula índices falsos
    soma_e_falsos = 0
    for i in range(n):
        if i == indice_real:
            continue
        e_i = secrets.randbelow(Q)
        s_i = secrets.randbelow(Q)
        # t_i = g^{s_i} * y_i^{-e_i}
        t_i = (mod_pow(G, s_i, P) * mod_inv(mod_pow(ys[i], e_i, P), P)) % P
        ts[i], es[i], ss[i] = t_i, e_i, s_i
        soma_e_falsos = (soma_e_falsos + e_i) % Q

    # Índice real: compromisso honesto
    r = secrets.randbelow(Q - 1) + 1
    ts[indice_real] = mod_pow(G, r, P)

    # Desafio global Fiat–Shamir
    parts = [int_to_bytes(G)]
    for y in ys:
        parts.append(int_to_bytes(y))
    for t in ts:
        parts.append(int_to_bytes(t))
    parts.append(contexto)
    E = hash_desafio(*parts)

    es[indice_real] = (E - soma_e_falsos) % Q
    ss[indice_real] = (r + es[indice_real] * x_real) % Q

    return SchnorrORProof(ts=ts, es=es, ss=ss)


def schnorr_or_verificar(
    ys: Sequence[int],
    prova: SchnorrORProof,
    contexto: bytes = b"urna-or",
) -> bool:
    """Verifica a prova OR CDS."""
    n = len(ys)
    if not (len(prova.ts) == len(prova.es) == len(prova.ss) == n):
        return False

    # Cada equação Schnorr
    for i in range(n):
        esquerda = mod_pow(G, prova.ss[i], P)
        direita = (prova.ts[i] * mod_pow(ys[i], prova.es[i], P)) % P
        if esquerda != direita:
            return False

    # Soma dos desafios = H(...)
    parts = [int_to_bytes(G)]
    for y in ys:
        parts.append(int_to_bytes(y))
    for t in prova.ts:
        parts.append(int_to_bytes(t))
    parts.append(contexto)
    E = hash_desafio(*parts)
    soma = sum(e % Q for e in prova.es) % Q
    return soma == E


# ---------------------------------------------------------------------------
# Ligação com a urna: candidatos → pontos públicos y_i
# ---------------------------------------------------------------------------

def segredo_candidato(candidato_id: int, eleicao_id: int) -> int:
    """
    Deriva o expoente x_i do candidato (conhecido pela urna ao registrar o voto).
    Em um sistema real, isso viria de um setup com chaves por candidato.
    """
    h = hashlib.sha256(f"cand:{eleicao_id}:{candidato_id}".encode()).digest()
    return int.from_bytes(h, "big") % Q


def ponto_candidato(candidato_id: int, eleicao_id: int) -> int:
    """y_i = g^{x_i} público na lista oficial da eleição."""
    return mod_pow(G, segredo_candidato(candidato_id, eleicao_id), P)


def provar_voto_valido(
    candidato_id: int,
    candidatos_ids: Sequence[int],
    eleicao_id: int,
) -> dict:
    """
    Gera prova OR: "votei em um dos candidatos oficiais".
    Retorna prova serializável + lista de y_i (pública).
    """
    if candidato_id not in candidatos_ids:
        raise ValueError("Candidato não está na lista oficial")

    ys = [ponto_candidato(cid, eleicao_id) for cid in candidatos_ids]
    indice = list(candidatos_ids).index(candidato_id)
    x = segredo_candidato(candidato_id, eleicao_id)
    contexto = f"eleicao:{eleicao_id}".encode()
    prova = schnorr_or_provar(ys, indice, x, contexto)

    return {
        "eleicao_id": eleicao_id,
        "candidatos_ids": list(candidatos_ids),
        "ys": ys,
        "prova": {
            "ts": prova.ts,
            "es": prova.es,
            "ss": prova.ss,
        },
        "contexto": contexto.decode(),
    }


def verificar_prova_voto_valido(pacote: dict) -> bool:
    """Verifica prova OR de voto em candidato da lista oficial."""
    ys = pacote["ys"]
    p = pacote["prova"]
    prova = SchnorrORProof(ts=p["ts"], es=p["es"], ss=p["ss"])
    contexto = pacote.get("contexto", "").encode()
    return schnorr_or_verificar(ys, prova, contexto)


def prova_para_json(pacote: dict) -> str:
    """Serializa prova (inteiros grandes → string decimal)."""
    def conv(obj):
        if isinstance(obj, int):
            return str(obj)
        if isinstance(obj, list):
            return [conv(x) for x in obj]
        if isinstance(obj, dict):
            return {k: conv(v) for k, v in obj.items()}
        return obj
    return json.dumps(conv(pacote), indent=2)


# ---------------------------------------------------------------------------
# Demo interativa / testes
# ---------------------------------------------------------------------------

def _demo():
    print("=" * 60)
    print("DEMO: Schnorr simples")
    print("=" * 60)
    x = secrets.randbelow(Q - 1) + 1
    y, prova = schnorr_provar(x, contexto=b"demo-simples")
    ok = schnorr_verificar(y, prova, contexto=b"demo-simples")
    print(f"  y = g^x  verificado? {ok}")

    print()
    print("=" * 60)
    print("DEMO: Prova OR de Schnorr (3 candidatos)")
    print("=" * 60)
    eleicao_id = 1
    candidatos = [10, 20, 30]  # ids
    voto_real = 20
    print(f"  Candidatos oficiais: {candidatos}")
    print(f"  Voto real (secreto): {voto_real}")

    pacote = provar_voto_valido(voto_real, candidatos, eleicao_id)
    ok = verificar_prova_voto_valido(pacote)
    print(f"  Prova OR verificada? {ok}")

    # Tentativa de fraude: candidato fora da lista
    print()
    print("  Tentativa com candidato inválido (99):")
    try:
        provar_voto_valido(99, candidatos, eleicao_id)
        print("  ERRO: deveria ter falhado")
    except ValueError as e:
        print(f"  Bloqueado corretamente: {e}")

    # Alterar prova quebra verificação
    print()
    print("  Adulterando um desafio e_i da prova:")
    pacote2 = json.loads(json.dumps(pacote))  # deep-ish via tipos nativos — ys são int
    # reconstruir com ints
    pacote_bad = provar_voto_valido(voto_real, candidatos, eleicao_id)
    pacote_bad["prova"]["es"][0] = (pacote_bad["prova"]["es"][0] + 1) % Q
    ok_bad = verificar_prova_voto_valido(pacote_bad)
    print(f"  Prova adulterada aceita? {ok_bad} (esperado: False)")

    print()
    print("=" * 60)
    print("Ideia na urna:")
    print("  1. Ao votar no candidato j, a cabine gera prova OR")
    print("  2. A prova vai junto do compromisso (sem revelar j)")
    print("  3. Qualquer auditor verifica: voto ∈ lista oficial")
    print("  4. Só na apuração a abertura revela j")
    print("=" * 60)


if __name__ == "__main__":
    _demo()
