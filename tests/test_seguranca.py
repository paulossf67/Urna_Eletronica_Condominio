"""Senhas, rate limit, sessões administrativas e anti-replay remoto."""

import time

import pytest

import database
from database import (
    alterar_senha_admin,
    bloqueado_por_tentativas,
    criar_admin,
    criar_sessao_admin,
    desativar_admin,
    encerrar_sessao_admin,
    hash_e_legado,
    hash_senha,
    validar_sessao_admin,
    verificar_admin,
    verificar_senha,
)


# ---------- hashing ----------

def test_hash_usa_pbkdf2_com_salt():
    h = hash_senha("senha-secreta")
    assert h.startswith("pbkdf2_sha256$")
    assert not hash_e_legado(h)
    assert "senha-secreta" not in h


def test_hashes_da_mesma_senha_sao_diferentes():
    """Salt aleatório: dois admins com a mesma senha têm hashes distintos."""
    assert hash_senha("igual") != hash_senha("igual")


def test_verificar_senha():
    h = hash_senha("correta")
    assert verificar_senha("correta", h)
    assert not verificar_senha("errada", h)
    assert not verificar_senha("", h)


def test_verificar_senha_aceita_formato_legado():
    import hashlib
    legado = hashlib.sha256(b"antiga").hexdigest()
    assert hash_e_legado(legado)
    assert verificar_senha("antiga", legado)
    assert not verificar_senha("outra", legado)


def test_login_migra_hash_legado_para_pbkdf2():
    import hashlib
    with database.db_session(escrita=True) as conn:
        conn.execute(
            "UPDATE administradores SET senha_hash = ?, trocar_senha = 0 WHERE usuario = 'admin'",
            (hashlib.sha256(b"admin123").hexdigest(),),
        )

    assert verificar_admin("admin", "admin123") is not None

    with database.db_session() as conn:
        novo = conn.execute(
            "SELECT senha_hash FROM administradores WHERE usuario = 'admin'"
        ).fetchone()["senha_hash"]
    assert not hash_e_legado(novo)
    assert verificar_admin("admin", "admin123") is not None


def test_admin_padrao_exige_troca_de_senha():
    admin = verificar_admin("admin", "admin123")
    assert admin["trocar_senha"] is True


def test_verificar_admin_nao_devolve_o_hash():
    admin = verificar_admin("admin", "admin123")
    assert "senha_hash" not in admin


# ---------- política de senha ----------

def test_senha_curta_e_recusada():
    with pytest.raises(ValueError, match="8 caracteres"):
        alterar_senha_admin("admin", "admin123", "curta")


def test_senha_nova_igual_a_atual_e_recusada():
    with pytest.raises(ValueError, match="diferente da atual"):
        alterar_senha_admin("admin", "admin123", "admin123")


def test_troca_de_senha_limpa_a_flag_e_as_sessoes():
    admin = verificar_admin("admin", "admin123")
    sessao = criar_sessao_admin(admin["id"])
    assert validar_sessao_admin(sessao["token"])

    assert alterar_senha_admin("admin", "admin123", "nova-senha-forte")

    assert validar_sessao_admin(sessao["token"]) is None   # sessão derrubada
    assert verificar_admin("admin", "admin123") is None
    novo = verificar_admin("admin", "nova-senha-forte")
    assert novo["trocar_senha"] is False


# ---------- rate limit ----------

def test_login_bloqueia_apos_tentativas_seguidas():
    for _ in range(database.MAX_TENTATIVAS_LOGIN):
        assert verificar_admin("admin", "errada") is None

    assert bloqueado_por_tentativas("admin", "admin") > 0

    # Mesmo com a senha certa, fica bloqueado durante a janela
    with pytest.raises(PermissionError, match="Muitas tentativas"):
        verificar_admin("admin", "admin123")


def test_bloqueio_e_por_usuario():
    for _ in range(database.MAX_TENTATIVAS_LOGIN):
        verificar_admin("admin", "errada")
    criar_admin("segundo", "senha-do-segundo", "Segundo Admin")
    # O bloqueio do 'admin' não atinge outro usuário
    assert verificar_admin("segundo", "senha-do-segundo") is not None


def test_acerto_dentro_da_janela_libera():
    for _ in range(database.MAX_TENTATIVAS_LOGIN - 1):
        verificar_admin("admin", "errada")
    assert bloqueado_por_tentativas("admin", "admin") == 0
    assert verificar_admin("admin", "admin123") is not None
    assert bloqueado_por_tentativas("admin", "admin") == 0


# ---------- sessões administrativas ----------

def test_sessao_admin_valida_e_expira_no_logout():
    admin = verificar_admin("admin", "admin123")
    sessao = criar_sessao_admin(admin["id"])
    dados = validar_sessao_admin(sessao["token"])
    assert dados["usuario"] == "admin"

    assert encerrar_sessao_admin(sessao["token"])
    assert validar_sessao_admin(sessao["token"]) is None


def test_token_invalido_nao_autentica():
    assert validar_sessao_admin("token-inventado") is None
    assert validar_sessao_admin("") is None
    assert validar_sessao_admin(None) is None


def test_apenas_o_hash_do_token_e_guardado():
    admin = verificar_admin("admin", "admin123")
    sessao = criar_sessao_admin(admin["id"])
    with database.db_session() as conn:
        linhas = [dict(r) for r in conn.execute("SELECT * FROM sessoes_admin").fetchall()]
    assert len(linhas) == 1
    assert sessao["token"] not in str(linhas[0])


def test_desativar_admin_derruba_sessao():
    criar_admin("temporario", "senha-temporaria", "Temp")
    admin = verificar_admin("temporario", "senha-temporaria")
    sessao = criar_sessao_admin(admin["id"])
    assert validar_sessao_admin(sessao["token"])

    desativar_admin("temporario")
    assert validar_sessao_admin(sessao["token"]) is None
    assert verificar_admin("temporario", "senha-temporaria") is None


def test_nao_desativa_o_ultimo_admin():
    with pytest.raises(ValueError, match="último administrador"):
        desativar_admin("admin")


def test_criar_admin_recusa_usuario_repetido():
    criar_admin("repetido", "senha-boa-123", "Um")
    with pytest.raises(ValueError, match="Já existe"):
        criar_admin("repetido", "outra-senha-123", "Dois")


# ---------- votação remota ----------

def test_desafio_e_consumido_na_autenticacao(eleicao_simples):
    import remoto_seguro
    from eleicao import autenticar_eleitor

    e = autenticar_eleitor(eleicao_simples, "123.456.789-09")
    d = remoto_seguro.criar_desafio(eleicao_simples)
    remoto_seguro.criar_sessao(eleicao_simples, e["id"], "12345678909", d["challenge_id"])

    # O mesmo desafio não serve duas vezes
    with pytest.raises(ValueError, match="Desafio inválido"):
        remoto_seguro.criar_sessao(eleicao_simples, e["id"], "12345678909", d["challenge_id"])


def test_nonce_repetido_e_recusado(eleicao_simples):
    import remoto_seguro
    from eleicao import autenticar_eleitor

    e = autenticar_eleitor(eleicao_simples, "123.456.789-09")
    d = remoto_seguro.criar_desafio(eleicao_simples)
    s = remoto_seguro.criar_sessao(eleicao_simples, e["id"], "12345678909", d["challenge_id"])

    nonce = remoto_seguro.gerar_nonce_voto()
    remoto_seguro.consumir_sessao(s["token"], nonce, manter_ativa=True)
    with pytest.raises(ValueError, match="Nonce já utilizado"):
        remoto_seguro.consumir_sessao(s["token"], nonce, manter_ativa=True)


def test_nonces_persistem_no_banco(eleicao_simples):
    """Regressão: antes os nonces viviam num set() que era esvaziado ao encher."""
    import remoto_seguro
    from eleicao import autenticar_eleitor

    e = autenticar_eleitor(eleicao_simples, "123.456.789-09")
    d = remoto_seguro.criar_desafio(eleicao_simples)
    s = remoto_seguro.criar_sessao(eleicao_simples, e["id"], "12345678909", d["challenge_id"])
    nonce = remoto_seguro.gerar_nonce_voto()
    remoto_seguro.consumir_sessao(s["token"], nonce, manter_ativa=True)

    with database.db_session() as conn:
        achou = conn.execute(
            "SELECT 1 FROM nonces_remotos WHERE nonce = ?", (nonce,)
        ).fetchone()
    assert achou is not None


def test_sessao_consumida_nao_autoriza_novo_voto(eleicao_simples):
    import remoto_seguro
    from eleicao import autenticar_eleitor

    e = autenticar_eleitor(eleicao_simples, "123.456.789-09")
    d = remoto_seguro.criar_desafio(eleicao_simples)
    s = remoto_seguro.criar_sessao(eleicao_simples, e["id"], "12345678909", d["challenge_id"])

    remoto_seguro.consumir_sessao(s["token"], remoto_seguro.gerar_nonce_voto())
    with pytest.raises(ValueError, match="já utilizada"):
        remoto_seguro.validar_sessao(s["token"], eleicao_simples, e["id"])


def test_token_com_assinatura_adulterada_e_recusado(eleicao_simples):
    import remoto_seguro
    from eleicao import autenticar_eleitor

    e = autenticar_eleitor(eleicao_simples, "123.456.789-09")
    d = remoto_seguro.criar_desafio(eleicao_simples)
    s = remoto_seguro.criar_sessao(eleicao_simples, e["id"], "12345678909", d["challenge_id"])

    tid, sig = s["token"].split(".")
    falso = f"{tid}.{'0' * len(sig)}"
    with pytest.raises(ValueError, match="Assinatura"):
        remoto_seguro.validar_sessao(falso, eleicao_simples, e["id"])


def test_sessao_nao_serve_para_outro_eleitor(eleicao_simples):
    import remoto_seguro
    from eleicao import autenticar_eleitor

    e1 = autenticar_eleitor(eleicao_simples, "123.456.789-09")
    e2 = autenticar_eleitor(eleicao_simples, "234.567.890-92")
    d = remoto_seguro.criar_desafio(eleicao_simples)
    s = remoto_seguro.criar_sessao(eleicao_simples, e1["id"], "12345678909", d["challenge_id"])

    with pytest.raises(ValueError, match="não autoriza"):
        remoto_seguro.validar_sessao(s["token"], eleicao_simples, e2["id"])


def test_sessao_expirada_e_recusada(eleicao_simples, monkeypatch):
    import remoto_seguro
    from eleicao import autenticar_eleitor

    e = autenticar_eleitor(eleicao_simples, "123.456.789-09")
    d = remoto_seguro.criar_desafio(eleicao_simples)
    s = remoto_seguro.criar_sessao(eleicao_simples, e["id"], "12345678909", d["challenge_id"])

    futuro = time.time() + remoto_seguro.TTL_SESSAO + 10
    monkeypatch.setattr(remoto_seguro.time, "time", lambda: futuro)
    with pytest.raises(ValueError, match="inválida|expirada"):
        remoto_seguro.validar_sessao(s["token"], eleicao_simples, e["id"])
