"""
Criptografia homomórfica aditiva (Paillier) para apuração de eleições
====================================================================

Propriedade usada:
  Dec(Enc(a) * Enc(b)  mod n²) = a + b

Assim podemos SOMAR votos cifrados sem abrir cada boletim.
Só o total de cada candidato é decifrado no fim.

Fluxo na urna:
  1. Abertura: gera par Paillier; inicia contador Enc(0) por candidato/branco/nulo
  2. Cada voto: contador[escolha] = contador[escolha] ⊕ Enc(peso)
  3. Apuração: Dec(contador[i]) → total (ou peso total) do candidato i

Referência: Paillier, Public-Key Cryptosystems Based on Composite Degree Residuosity (1999).

AVISO: chaves de demo usam primos ~512 bits (rápido para estudo).
       Em produção use ≥ 2048 bits.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Aritmética
# ---------------------------------------------------------------------------

def _is_prime(n: int, rounds: int = 16) -> bool:
    if n < 2:
        return False
    for s in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31):
        if n == s:
            return True
        if n % s == 0:
            return False
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _gen_prime(bits: int) -> int:
    while True:
        n = secrets.randbits(bits) | (1 << (bits - 1)) | 1
        if _is_prime(n):
            return n


def _lcm(a: int, b: int) -> int:
    return abs(a * b) // math_gcd(a, b)


def math_gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return a


def _L(u: int, n: int) -> int:
    return (u - 1) // n


# ---------------------------------------------------------------------------
# Paillier
# ---------------------------------------------------------------------------

@dataclass
class PaillierPublicKey:
    n: int
    g: int  # g = n + 1 (forma comum)

    @property
    def n2(self) -> int:
        return self.n * self.n

    def encrypt(self, m: int) -> int:
        """Enc(m) com m em 0..n-1."""
        if m < 0:
            raise ValueError("mensagem negativa não suportada neste esquema")
        m = m % self.n
        # r coprimo com n
        while True:
            r = secrets.randbelow(self.n - 1) + 1
            if math_gcd(r, self.n) == 1:
                break
        # c = g^m * r^n  mod n²
        return (pow(self.g, m, self.n2) * pow(r, self.n, self.n2)) % self.n2

    def add(self, c1: int, c2: int) -> int:
        """Homomorfismo aditivo: Enc(m1) ⊕ Enc(m2) = Enc(m1+m2)."""
        return (c1 * c2) % self.n2

    def add_plain(self, c: int, m: int) -> int:
        """Enc(m1) ⊕ Enc(m) com m em claro (via Enc(m))."""
        return self.add(c, self.encrypt(m))

    def to_dict(self) -> dict:
        return {"n": str(self.n), "g": str(self.g)}

    @staticmethod
    def from_dict(d: dict) -> "PaillierPublicKey":
        return PaillierPublicKey(n=int(d["n"]), g=int(d["g"]))


@dataclass
class PaillierPrivateKey:
    pub: PaillierPublicKey
    lam: int  # λ = lcm(p-1, q-1)
    mu: int   # μ = (L(g^λ mod n²))^{-1} mod n

    def decrypt(self, c: int) -> int:
        n = self.pub.n
        n2 = self.pub.n2
        u = pow(c, self.lam, n2)
        return (_L(u, n) * self.mu) % n

    def to_dict(self) -> dict:
        return {
            "pub": self.pub.to_dict(),
            "lam": str(self.lam),
            "mu": str(self.mu),
        }

    @staticmethod
    def from_dict(d: dict) -> "PaillierPrivateKey":
        pub = PaillierPublicKey.from_dict(d["pub"])
        return PaillierPrivateKey(pub=pub, lam=int(d["lam"]), mu=int(d["mu"]))


def gerar_chaves(bits: int = 512) -> tuple[PaillierPublicKey, PaillierPrivateKey]:
    """
    Gera par Paillier.
    bits = tamanho de n em bits (p e q com bits/2 cada).
    """
    half = bits // 2
    p = _gen_prime(half)
    q = _gen_prime(half)
    while q == p:
        q = _gen_prime(half)
    n = p * q
    g = n + 1  # simplifica: L(g^λ) = λ, μ = λ^{-1} mod n quando p,q mesmo tamanho
    lam = _lcm(p - 1, q - 1)
    n2 = n * n
    mu = pow(_L(pow(g, lam, n2), n), -1, n)
    pub = PaillierPublicKey(n=n, g=g)
    priv = PaillierPrivateKey(pub=pub, lam=lam, mu=mu)
    return pub, priv


# ---------------------------------------------------------------------------
# Persistência das chaves da urna
# ---------------------------------------------------------------------------

_KEY_PATH = os.environ.get(
    "URNA_PAILLIER",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "urna_paillier.json"),
)

# Tamanho de n para chaves NOVAS. 2048 bits é o mínimo aceitável fora de demo;
# use URNA_PAILLIER_BITS=512 apenas para estudo/testes rápidos.
BITS_PADRAO = int(os.environ.get("URNA_PAILLIER_BITS", "2048"))
BITS_MINIMO_SEGURO = 2048


def salvar_chaves(pub: PaillierPublicKey, priv: PaillierPrivateKey, caminho: str | None = None) -> str:
    path = caminho or _KEY_PATH
    data = {"publica": pub.to_dict(), "privada": priv.to_dict()}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def carregar_chaves(caminho: str | None = None) -> tuple[PaillierPublicKey, PaillierPrivateKey] | None:
    path = caminho or _KEY_PATH
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    pub = PaillierPublicKey.from_dict(data["publica"])
    priv = PaillierPrivateKey.from_dict(data["privada"])
    return pub, priv


def carregar_ou_gerar(bits: int | None = None) -> tuple[PaillierPublicKey, PaillierPrivateKey]:
    """
    Carrega as chaves da urna ou gera um par novo.
    Chaves já existentes são mantidas como estão (trocá-las inutilizaria os
    contadores homomórficos já acumulados numa eleição em andamento).
    """
    par = carregar_chaves()
    if par:
        return par
    pub, priv = gerar_chaves(bits or BITS_PADRAO)
    salvar_chaves(pub, priv)
    return pub, priv


def info_homo() -> dict[str, Any]:
    par = carregar_chaves()
    n_bits = par[0].n.bit_length() if par else None
    info: dict[str, Any] = {
        "esquema": "Paillier (aditivo)",
        "propriedade": "Dec(Enc(a)*Enc(b)) = a+b",
        "chave_existe": par is not None,
        "arquivo": os.path.abspath(_KEY_PATH),
        "n_bits": n_bits,
        "bits_novas_chaves": BITS_PADRAO,
        "segura": bool(n_bits and n_bits >= BITS_MINIMO_SEGURO),
    }
    if n_bits and n_bits < BITS_MINIMO_SEGURO:
        info["aviso"] = (
            f"A chave atual tem {n_bits} bits — tamanho de demonstração. "
            f"Para uso real gere uma chave nova com pelo menos {BITS_MINIMO_SEGURO} bits "
            f"(apague {os.path.basename(_KEY_PATH)} ANTES de abrir a eleição)."
        )
    return info


# ---------------------------------------------------------------------------
# Contadores homomórficos de uma eleição
# ---------------------------------------------------------------------------

def iniciar_contadores(pub: PaillierPublicKey, chaves: list[str]) -> dict[str, str]:
    """
    Inicia contador Enc(0) para cada chave (ex: 'cand:1', 'branco', 'nulo').
    Retorna dict chave → ciphertext (decimal string).
    """
    zero = pub.encrypt(0)
    return {k: str(zero) for k in chaves}


def acumular_voto(
    pub: PaillierPublicKey,
    contadores: dict[str, str],
    escolha: str,
    peso: int = 1,
) -> dict[str, str]:
    """
    Adiciona Enc(peso) ao contador da escolha.
    escolha: 'cand:ID' | 'branco' | 'nulo'
    """
    if escolha not in contadores:
        raise ValueError(f"Contador desconhecido: {escolha}")
    c_atual = int(contadores[escolha])
    c_novo = pub.add_plain(c_atual, peso)
    out = dict(contadores)
    out[escolha] = str(c_novo)
    return out


def decifrar_contadores(
    priv: PaillierPrivateKey,
    contadores: dict[str, str],
) -> dict[str, int]:
    """Abre todos os totais (só na apuração)."""
    return {k: priv.decrypt(int(c)) for k, c in contadores.items()}


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def _demo():
    print("=" * 60)
    print("DEMO: Paillier — apuração homomórfica")
    print("=" * 60)
    print("  Gerando chaves (512 bits, aguarde)...")
    pub, priv = gerar_chaves(512)
    print(f"  n tem {pub.n.bit_length()} bits")

    # 3 candidatos
    cont = iniciar_contadores(pub, ["cand:10", "cand:20", "cand:30", "branco", "nulo"])

    # Simula votos: 10, 10, 20, branco, 10 (peso 2)
    votos = [
        ("cand:10", 1),
        ("cand:10", 1),
        ("cand:20", 1),
        ("branco", 1),
        ("cand:10", 2),  # peso 2
    ]
    print(f"  Aplicando {len(votos)} votos cifrados...")
    for esc, peso in votos:
        cont = acumular_voto(pub, cont, esc, peso)
        # Durante a soma, os contadores continuam opacos:
        assert isinstance(cont[esc], str) and len(cont[esc]) > 20

    print("  Decifrando totais (só o resultado final)...")
    totais = decifrar_contadores(priv, cont)
    for k, v in sorted(totais.items()):
        print(f"    {k}: {v}")

    assert totais["cand:10"] == 4  # 1+1+2
    assert totais["cand:20"] == 1
    assert totais["cand:30"] == 0
    assert totais["branco"] == 1
    print("  Verificação aritmética: OK")
    print("=" * 60)


if __name__ == "__main__":
    _demo()
