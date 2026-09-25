"""Validação de CPF pelo algoritmo oficial."""

import pytest

from eleicao import formatar_cpf, gerar_cpf_valido, mascarar_cpf, normalizar_cpf, validar_cpf


@pytest.mark.parametrize("cpf", [
    "123.456.789-09",
    "12345678909",
    "234.567.890-92",
    "345.678.901-75",
    "456.789.012-49",
    "567.890.123-03",
    "678.901.234-69",
])
def test_cpfs_validos(cpf):
    assert validar_cpf(cpf)


@pytest.mark.parametrize("cpf", [
    "123.456.789-00",   # dígitos verificadores errados
    "111.111.111-11",   # sequência repetida
    "000.000.000-00",
    "1234567890",       # 10 dígitos
    "123456789012",     # 12 dígitos
    "",
    None,
    "abc.def.ghi-jk",
])
def test_cpfs_invalidos(cpf):
    assert not validar_cpf(cpf)


def test_normalizar_remove_pontuacao():
    assert normalizar_cpf(" 123.456.789-09 ") == "12345678909"


def test_formatar():
    assert formatar_cpf("12345678909") == "123.456.789-09"


def test_mascarar_esconde_o_meio():
    m = mascarar_cpf("12345678909")
    assert m == "123.***.***-09"
    assert "456" not in m


def test_gerar_cpf_valido_produz_dv_correto():
    for base in ("123456789", "000000001", "987654321"):
        assert validar_cpf(gerar_cpf_valido(base))
