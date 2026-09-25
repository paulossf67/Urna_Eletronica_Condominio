"""Criptografia dos votos, compromissos, Merkle, Schnorr OR e Paillier."""

import pytest

from crypto_voto import cifrar_voto, decifrar_voto
from homo_voto import acumular_voto, carregar_ou_gerar, decifrar_contadores, iniciar_contadores
from schnorr_or import prova_para_json, provar_voto_valido, verificar_prova_voto_valido
from zk_voto import (
    construir_merkle,
    criar_compromisso,
    prova_merkle,
    verificar_compromisso,
    verificar_prova_merkle,
)


# ---------- compromisso ----------

def test_compromisso_abre_com_a_mesma_abertura():
    c = criar_compromisso("candidato", 7, 1.5, cargo="Síndico")
    assert verificar_compromisso(c["compromisso"], "candidato", 7, 1.5,
                                 c["nonce"], "Síndico")


def test_compromisso_nao_abre_com_candidato_diferente():
    c = criar_compromisso("candidato", 7, 1.5, cargo="Síndico")
    assert not verificar_compromisso(c["compromisso"], "candidato", 8, 1.5,
                                     c["nonce"], "Síndico")


def test_compromisso_amarra_o_cargo():
    """Um voto não pode ser transplantado de um cargo para outro."""
    c = criar_compromisso("candidato", 7, 1.0, cargo="Síndico")
    assert not verificar_compromisso(c["compromisso"], "candidato", 7, 1.0,
                                     c["nonce"], "Conselheiro")


def test_compromisso_esconde_o_voto():
    """Dois votos iguais geram hashes diferentes (nonce aleatório)."""
    a = criar_compromisso("candidato", 7, 1.0, cargo="X")
    b = criar_compromisso("candidato", 7, 1.0, cargo="X")
    assert a["compromisso"] != b["compromisso"]


# ---------- cifra ----------

def test_cifrar_decifrar_roundtrip():
    p = cifrar_voto("candidato", 42, 2.5, cargo="Conselheiro")
    aberto = decifrar_voto(p["voto_cifrado"], p["integridade"])
    assert aberto["tipo"] == "candidato"
    assert aberto["candidato_id"] == 42
    assert aberto["peso"] == 2.5
    assert aberto["cargo"] == "Conselheiro"


def test_cifrado_nao_vaza_o_candidato_em_claro():
    p = cifrar_voto("candidato", 424242, 1.0, cargo="Síndico")
    assert "424242" not in p["voto_cifrado"]
    assert "Síndico" not in p["voto_cifrado"]


def test_hmac_detecta_adulteracao():
    p = cifrar_voto("branco", None, 1.0, cargo="Síndico")
    with pytest.raises(ValueError, match="Integridade"):
        decifrar_voto(p["voto_cifrado"], "0" * 64)


def test_ciphertext_adulterado_falha():
    p = cifrar_voto("branco", None, 1.0, cargo="Síndico")
    corrompido = p["voto_cifrado"][:-4] + "AAAA"
    with pytest.raises(ValueError):
        decifrar_voto(corrompido, None)


# ---------- Merkle ----------

def test_merkle_prova_de_inclusao():
    comps = [f"{i:064x}" for i in range(7)]
    arvore = construir_merkle(comps)
    for i, c in enumerate(comps):
        prova = prova_merkle(comps, i)
        assert prova["raiz"] == arvore["raiz"]
        assert verificar_prova_merkle(c, prova)


def test_merkle_rejeita_compromisso_fora_do_quadro():
    comps = [f"{i:064x}" for i in range(4)]
    prova = prova_merkle(comps, 0)
    assert not verificar_prova_merkle("f" * 64, prova)


def test_merkle_muda_se_o_quadro_muda():
    a = construir_merkle([f"{i:064x}" for i in range(4)])
    b = construir_merkle([f"{i:064x}" for i in range(5)])
    assert a["raiz"] != b["raiz"]


def test_merkle_vazio_tem_raiz_definida():
    assert construir_merkle([])["raiz"]


# ---------- Schnorr OR ----------

def test_prova_or_valida_para_candidato_da_lista():
    ids = [10, 20, 30]
    assert verificar_prova_voto_valido(provar_voto_valido(20, ids, 1))


def test_prova_or_recusa_candidato_fora_da_lista():
    with pytest.raises(ValueError):
        provar_voto_valido(99, [10, 20, 30], 1)


def test_prova_or_sobrevive_a_serializacao_json():
    import json

    from eleicao import _json_ints

    pacote = provar_voto_valido(30, [10, 20, 30], 1)
    restaurado = _json_ints(json.loads(prova_para_json(pacote)))
    assert verificar_prova_voto_valido(restaurado)


def test_prova_or_falha_se_adulterada():
    pacote = provar_voto_valido(20, [10, 20, 30], 1)
    pacote["prova"]["ss"][0] = (pacote["prova"]["ss"][0] + 1)
    assert not verificar_prova_voto_valido(pacote)


def test_prova_or_nao_revela_o_indice_escolhido():
    """A prova tem a mesma forma para qualquer escolha — nada distingue o real."""
    ids = [10, 20, 30]
    p1 = provar_voto_valido(10, ids, 1)
    p2 = provar_voto_valido(30, ids, 1)
    assert sorted(p1.keys()) == sorted(p2.keys())
    for campo in ("ts", "es", "ss"):
        assert len(p1["prova"][campo]) == len(p2["prova"][campo]) == len(ids)


# ---------- Paillier ----------

def test_paillier_soma_homomorfica():
    pub, priv = carregar_ou_gerar()
    cont = iniciar_contadores(pub, ["cand:1", "cand:2", "branco:X"])
    for _ in range(3):
        cont = acumular_voto(pub, cont, "cand:1", 100)   # peso 1.00
    cont = acumular_voto(pub, cont, "cand:1", 250)       # peso 2.50
    cont = acumular_voto(pub, cont, "cand:2", 100)
    cont = acumular_voto(pub, cont, "branco:X", 100)

    totais = decifrar_contadores(priv, cont)
    assert totais["cand:1"] == 550   # 3×100 + 250
    assert totais["cand:2"] == 100
    assert totais["branco:X"] == 100


def test_paillier_contadores_permanecem_opacos():
    pub, _ = carregar_ou_gerar()
    cont = iniciar_contadores(pub, ["cand:1"])
    zero = cont["cand:1"]
    cont = acumular_voto(pub, cont, "cand:1", 100)
    assert cont["cand:1"] != zero
    assert len(cont["cand:1"]) > 20
