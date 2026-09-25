"""
Módulo de gerenciamento de eleições, candidatos, eleitores e resultados.
"""

from database import db_session, registrar_auditoria
from datetime import datetime
import csv
import json
import os
import re


# ==================== UTILITÁRIOS CPF ====================

def normalizar_cpf(cpf: str) -> str:
    """Remove pontuação e espaços do CPF, deixando só dígitos."""
    return re.sub(r"\D", "", (cpf or "").strip())


def validar_cpf(cpf: str) -> bool:
    """
    Valida CPF pelo algoritmo oficial (dígitos verificadores, módulo 11).
    Rejeita sequências repetidas (111.111.111-11 etc.).
    """
    cpf = normalizar_cpf(cpf)
    if len(cpf) != 11:
        return False
    if cpf == cpf[0] * 11:
        return False

    # Primeiro dígito verificador
    soma = sum(int(cpf[i]) * (10 - i) for i in range(9))
    resto = soma % 11
    digito1 = 0 if resto < 2 else 11 - resto
    if int(cpf[9]) != digito1:
        return False

    # Segundo dígito verificador
    soma = sum(int(cpf[i]) * (11 - i) for i in range(10))
    resto = soma % 11
    digito2 = 0 if resto < 2 else 11 - resto
    if int(cpf[10]) != digito2:
        return False

    return True


def gerar_cpf_valido(base9: str) -> str:
    """Gera CPF completo (11 dígitos) a partir de 9 dígitos base, com DV corretos."""
    base9 = normalizar_cpf(base9)[:9].ljust(9, "0")
    soma = sum(int(base9[i]) * (10 - i) for i in range(9))
    resto = soma % 11
    d1 = 0 if resto < 2 else 11 - resto
    base10 = base9 + str(d1)
    soma = sum(int(base10[i]) * (11 - i) for i in range(10))
    resto = soma % 11
    d2 = 0 if resto < 2 else 11 - resto
    return base10 + str(d2)


def formatar_cpf(cpf: str) -> str:
    """Formata CPF como 000.000.000-00."""
    cpf = normalizar_cpf(cpf)
    if len(cpf) != 11:
        return cpf
    return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"


# ==================== ELEIÇÕES ====================

def criar_eleicao(nome: str, descricao: str, tipo: str, voto_ponderado: bool = False) -> int:
    """Cria uma nova eleição. Retorna o ID."""
    tipos_validos = ("condominio", "associacao", "sindicato")
    if tipo not in tipos_validos:
        raise ValueError(f"Tipo inválido. Use: {', '.join(tipos_validos)}")

    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO eleicoes (nome, descricao, tipo, voto_ponderado)
               VALUES (?, ?, ?, ?)""",
            (nome, descricao, tipo, 1 if voto_ponderado else 0)
        )
        eleicao_id = cursor.lastrowid
        registrar_auditoria(
            conn, "CRIAR_ELEICAO",
            f"ID={eleicao_id} Nome={nome} Tipo={tipo} Ponderado={voto_ponderado}"
        )
        return eleicao_id


def listar_eleicoes(status: str | None = None) -> list[dict]:
    """Lista eleições, opcionalmente filtradas por status."""
    with db_session() as conn:
        cursor = conn.cursor()
        if status:
            cursor.execute(
                "SELECT * FROM eleicoes WHERE status = ? ORDER BY id DESC",
                (status,)
            )
        else:
            cursor.execute("SELECT * FROM eleicoes ORDER BY id DESC")
        return [dict(row) for row in cursor.fetchall()]


def obter_eleicao(eleicao_id: int) -> dict | None:
    """Retorna dados de uma eleição pelo ID."""
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM eleicoes WHERE id = ?", (eleicao_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def alterar_status_eleicao(eleicao_id: int, novo_status: str) -> bool:
    """Altera o status de uma eleição (preparacao / aberta / fechada)."""
    status_validos = ("preparacao", "aberta", "fechada")
    if novo_status not in status_validos:
        raise ValueError(f"Status inválido. Use: {', '.join(status_validos)}")

    with db_session() as conn:
        cursor = conn.cursor()
        eleicao = obter_eleicao(eleicao_id)
        if not eleicao:
            return False

        agora = datetime.now().isoformat(timespec="seconds")
        if novo_status == "aberta":
            # Inicia contadores homomórficos Paillier (Enc(0) por opção)
            try:
                contadores_json = _iniciar_contadores_homo(eleicao_id)
            except Exception:
                contadores_json = None
            cursor.execute(
                "UPDATE eleicoes SET status = ?, data_abertura = ?, contadores_homo = ? WHERE id = ?",
                (novo_status, agora, contadores_json, eleicao_id)
            )
        elif novo_status == "fechada":
            cursor.execute(
                "UPDATE eleicoes SET status = ?, data_fechamento = ? WHERE id = ?",
                (novo_status, agora, eleicao_id)
            )
        else:
            cursor.execute(
                "UPDATE eleicoes SET status = ? WHERE id = ?",
                (novo_status, eleicao_id)
            )

        registrar_auditoria(
            conn, "ALTERAR_STATUS",
            f"Eleição {eleicao_id}: {eleicao['status']} → {novo_status}"
        )
        return True


def _iniciar_contadores_homo(eleicao_id: int) -> str:
    """Cria contadores Enc(0) para cada candidato + branco + nulo."""
    from homo_voto import carregar_ou_gerar, iniciar_contadores
    pub, _priv = carregar_ou_gerar(512)
    cands = listar_candidatos(eleicao_id)
    chaves = [f"cand:{c['id']}" for c in cands] + ["branco", "nulo"]
    cont = iniciar_contadores(pub, chaves)
    return json.dumps(cont)


def criar_eleicao_demonstracao() -> int:
    """Cria uma eleição completa de demonstração com candidatos e eleitores."""
    eid = criar_eleicao(
        nome="Eleição do Síndico 2026 (Demonstração)",
        descricao="Eleição demonstrativa do condomínio Residencial Exemplo",
        tipo="condominio",
        voto_ponderado=True
    )

    # Candidatos
    cadastrar_candidato(eid, 10, "João Silva", "Síndico", "Proposta: melhorias na segurança e área de lazer")
    cadastrar_candidato(eid, 20, "Maria Santos", "Síndico", "Proposta: transparência nas contas e redução de taxas")
    cadastrar_candidato(eid, 30, "Pedro Oliveira", "Conselheiro", "Experiência em administração condominial")
    cadastrar_candidato(eid, 40, "Ana Costa", "Conselheiro", "Foco em sustentabilidade e economia de energia")

    # Eleitores com pesos diferentes (fração ideal)
    # CPFs válidos pelo algoritmo oficial (dígitos verificadores)
    eleitores_demo = [
        ("Carlos Mendes", "123.456.789-09", "101", "A", 1.0),
        ("Fernanda Lima", "234.567.890-92", "202", "A", 1.5),
        ("Roberto Alves", "345.678.901-75", "301", "B", 1.0),
        ("Juliana Rocha", "456.789.012-49", "102", "A", 2.0),
        ("Marcos Pereira", "567.890.123-03", "203", "B", 1.0),
        ("Patrícia Souza", "678.901.234-69", "401", "C", 1.25),
    ]
    for nome, cpf, un, bl, peso in eleitores_demo:
        cadastrar_eleitor(eid, nome, cpf, unidade=un, bloco=bl, peso=peso)

    return eid


# ==================== CANDIDATOS ====================

def cadastrar_candidato(eleicao_id: int, numero: int, nome: str, cargo: str, descricao: str = "") -> int:
    """Cadastra um candidato em uma eleição."""
    eleicao = obter_eleicao(eleicao_id)
    if not eleicao:
        raise ValueError("Eleição não encontrada.")
    if eleicao["status"] != "preparacao":
        raise ValueError("Só é possível cadastrar candidatos em eleições em preparação.")

    with db_session() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """INSERT INTO candidatos (eleicao_id, numero, nome, cargo, descricao)
                   VALUES (?, ?, ?, ?, ?)""",
                (eleicao_id, numero, nome, cargo, descricao)
            )
            candidato_id = cursor.lastrowid
            registrar_auditoria(
                conn, "CADASTRAR_CANDIDATO",
                f"Eleição={eleicao_id} Número={numero} Nome={nome} Cargo={cargo}"
            )
            return candidato_id
        except Exception as e:
            if "UNIQUE" in str(e):
                raise ValueError(f"Já existe candidato com o número {numero} nesta eleição.")
            raise


def listar_candidatos(eleicao_id: int, apenas_ativos: bool = True) -> list[dict]:
    """Lista candidatos de uma eleição."""
    with db_session() as conn:
        cursor = conn.cursor()
        if apenas_ativos:
            cursor.execute(
                """SELECT * FROM candidatos
                   WHERE eleicao_id = ? AND ativo = 1
                   ORDER BY cargo, numero""",
                (eleicao_id,)
            )
        else:
            cursor.execute(
                "SELECT * FROM candidatos WHERE eleicao_id = ? ORDER BY cargo, numero",
                (eleicao_id,)
            )
        return [dict(row) for row in cursor.fetchall()]


def obter_candidato_por_numero(eleicao_id: int, numero: int) -> dict | None:
    """Busca candidato pelo número na eleição."""
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT * FROM candidatos
               WHERE eleicao_id = ? AND numero = ? AND ativo = 1""",
            (eleicao_id, numero)
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def listar_cargos(eleicao_id: int) -> list[str]:
    """Retorna lista de cargos distintos da eleição."""
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT DISTINCT cargo FROM candidatos
               WHERE eleicao_id = ? AND ativo = 1 ORDER BY cargo""",
            (eleicao_id,)
        )
        return [row["cargo"] for row in cursor.fetchall()]


# ==================== ELEITORES ====================

def cadastrar_eleitor(
    eleicao_id: int,
    nome: str,
    documento: str,
    codigo_acesso: str = "",
    unidade: str = "",
    bloco: str = "",
    peso: float = 1.0
) -> int:
    """
    Cadastra um eleitor em uma eleição.
    O documento deve ser o CPF (com ou sem pontuação).
    O controle de quem já votou é feito pelo CPF.
    """
    eleicao = obter_eleicao(eleicao_id)
    if not eleicao:
        raise ValueError("Eleição não encontrada.")
    if eleicao["status"] == "fechada":
        raise ValueError("Não é possível cadastrar eleitores em eleição fechada.")

    if peso <= 0:
        raise ValueError("O peso do voto deve ser maior que zero.")

    cpf = normalizar_cpf(documento)
    if not validar_cpf(cpf):
        raise ValueError("CPF inválido. Informe 11 dígitos (ex: 123.456.789-00).")

    # Se não informar código, usa o próprio CPF (identificação única)
    codigo = (codigo_acesso or "").strip() or cpf

    with db_session() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """INSERT INTO eleitores
                   (eleicao_id, nome, documento, unidade, bloco, codigo_acesso, peso)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (eleicao_id, nome, cpf, unidade, bloco, codigo, peso)
            )
            eleitor_id = cursor.lastrowid
            registrar_auditoria(
                conn, "CADASTRAR_ELEITOR",
                f"Eleição={eleicao_id} Nome={nome} CPF={formatar_cpf(cpf)} Peso={peso}"
            )
            return eleitor_id
        except Exception as e:
            if "UNIQUE" in str(e):
                raise ValueError("Este CPF já está cadastrado nesta eleição.")
            raise


def listar_eleitores(eleicao_id: int) -> list[dict]:
    """Lista eleitores de uma eleição."""
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT id, nome, documento, unidade, bloco, peso, ja_votou, data_voto, ativo
               FROM eleitores WHERE eleicao_id = ? ORDER BY nome""",
            (eleicao_id,)
        )
        return [dict(row) for row in cursor.fetchall()]


def autenticar_eleitor(eleicao_id: int, cpf: str) -> dict | None:
    """
    Autentica eleitor pelo CPF.
    O controle de quem já votou é feito pelo CPF (campo documento).
    Aceita CPF com ou sem pontuação.
    """
    cpf_norm = normalizar_cpf(cpf)
    if not cpf_norm:
        return None

    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT * FROM eleitores
               WHERE eleicao_id = ? AND documento = ? AND ativo = 1""",
            (eleicao_id, cpf_norm)
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def marcar_como_votou(eleitor_id: int):
    """Marca o eleitor como tendo votado."""
    agora = datetime.now().isoformat(timespec="seconds")
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE eleitores SET ja_votou = 1, data_voto = ? WHERE id = ?",
            (agora, eleitor_id)
        )


# ==================== VOTAÇÃO ====================

def registrar_voto(
    eleicao_id: int,
    tipo_voto: str,
    candidato_id: int | None = None,
    peso: float = 1.0
) -> int:
    """
    Registra voto cifrado + compromisso + prova OR de Schnorr (se nominal).

    Para voto em candidato:
      - Gera prova OR: "este voto é em um candidato da lista oficial"
      - Verifica a prova antes de gravar
      - Armazena prova_or em JSON (verificável sem revelar o candidato)
    """
    if tipo_voto not in ("candidato", "branco", "nulo"):
        raise ValueError("Tipo de voto inválido.")

    if tipo_voto == "candidato" and not candidato_id:
        raise ValueError("Candidato é obrigatório para voto nominal.")

    from crypto_voto import cifrar_voto
    pacote = cifrar_voto(
        tipo_voto,
        candidato_id if tipo_voto == "candidato" else None,
        peso,
    )

    prova_or_json = None
    if tipo_voto == "candidato":
        # Lista oficial de IDs da eleição
        cands = listar_candidatos(eleicao_id)
        ids = [c["id"] for c in cands]
        if candidato_id not in ids:
            raise ValueError("Candidato não pertence a esta eleição.")

        from schnorr_or import provar_voto_valido, verificar_prova_voto_valido, prova_para_json

        pacote_or = provar_voto_valido(candidato_id, ids, eleicao_id)
        if not verificar_prova_voto_valido(pacote_or):
            raise ValueError("Falha interna: prova OR inválida após geração.")
        prova_or_json = prova_para_json(pacote_or)

    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO votos
               (eleicao_id, candidato_id, tipo_voto, peso, voto_cifrado, integridade, compromisso, prova_or)
               VALUES (?, NULL, NULL, ?, ?, ?, ?, ?)""",
            (
                eleicao_id,
                peso,
                pacote["voto_cifrado"],
                pacote["integridade"],
                pacote["compromisso"],
                prova_or_json,
            )
        )
        voto_id = cursor.lastrowid
        tem_or = "sim" if prova_or_json else "nao"

        # Acumula no contador homomórfico (sem abrir totais)
        try:
            _acumular_homo(cursor, eleicao_id, tipo_voto, candidato_id, peso)
        except Exception:
            pass

        registrar_auditoria(
            conn, "VOTO_REGISTRADO",
            f"Eleição={eleicao_id} Peso={peso} Cifrado=sim OR={tem_or} "
            f"Compromisso={pacote['compromisso'][:16]}…"
        )
        return voto_id


def _acumular_homo(cursor, eleicao_id: int, tipo_voto: str, candidato_id: int | None, peso: float):
    """Soma Enc(peso) no contador da escolha (Paillier)."""
    from homo_voto import carregar_ou_gerar, acumular_voto

    cursor.execute("SELECT contadores_homo FROM eleicoes WHERE id = ?", (eleicao_id,))
    row = cursor.fetchone()
    if not row or not row["contadores_homo"]:
        return
    cont = json.loads(row["contadores_homo"])
    pub, _ = carregar_ou_gerar(512)

    if tipo_voto == "candidato" and candidato_id:
        chave = f"cand:{candidato_id}"
    elif tipo_voto == "branco":
        chave = "branco"
    else:
        chave = "nulo"

    # peso como inteiro (centésimos se fracionário)
    peso_int = int(round(float(peso) * 100))
    cont = acumular_voto(pub, cont, chave, peso_int)
    cursor.execute(
        "UPDATE eleicoes SET contadores_homo = ? WHERE id = ?",
        (json.dumps(cont), eleicao_id),
    )


def obter_resultados_homo(eleicao_id: int) -> dict:
    """
    Apuração via criptografia homomórfica:
    decifra apenas os totais acumulados (não cada voto).
    Pesos foram armazenados em centésimos.
    """
    from homo_voto import carregar_ou_gerar, decifrar_contadores

    eleicao = obter_eleicao(eleicao_id)
    if not eleicao:
        raise ValueError("Eleição não encontrada.")
    if not eleicao.get("contadores_homo"):
        raise ValueError("Contadores homomórficos não inicializados (abra a eleição novamente).")

    pub, priv = carregar_ou_gerar(512)
    cont = json.loads(eleicao["contadores_homo"])
    totais_raw = decifrar_contadores(priv, cont)

    cands = listar_candidatos(eleicao_id)
    candidatos = []
    for c in cands:
        chave = f"cand:{c['id']}"
        peso_cent = totais_raw.get(chave, 0)
        candidatos.append({
            "id": c["id"],
            "numero": c["numero"],
            "nome": c["nome"],
            "cargo": c["cargo"],
            "peso_votos": peso_cent / 100.0,
        })
    candidatos.sort(key=lambda x: (-x["peso_votos"], x["numero"]))

    return {
        "eleicao_id": eleicao_id,
        "esquema": "Paillier aditivo",
        "candidatos": candidatos,
        "brancos_peso": totais_raw.get("branco", 0) / 100.0,
        "nulos_peso": totais_raw.get("nulo", 0) / 100.0,
        "totais_brutos_centésimos": totais_raw,
    }


def obter_recibo_voto(voto_id: int) -> dict | None:
    """Recibo público do voto (compromisso + se tem prova OR)."""
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT id, eleicao_id, compromisso, prova_or, registrado_em
               FROM votos WHERE id = ?""",
            (voto_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        d = dict(row)
        d["tem_prova_or"] = bool(d.get("prova_or"))
        return d


def verificar_provas_or(eleicao_id: int) -> dict:
    """
    Verifica em Python todas as provas OR de Schnorr da eleição.
    Não revela em quem cada um votou — só se a prova é válida.
    """
    from schnorr_or import verificar_prova_voto_valido

    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT id, prova_or, compromisso, registrado_em
               FROM votos WHERE eleicao_id = ? ORDER BY id""",
            (eleicao_id,),
        )
        rows = [dict(r) for r in cursor.fetchall()]

    total = len(rows)
    com_prova = 0
    validas = 0
    invalidas = []
    sem_prova = []  # branco/nulo ou legado

    for r in rows:
        if not r.get("prova_or"):
            sem_prova.append(r["id"])
            continue
        com_prova += 1
        try:
            pacote = json.loads(r["prova_or"])
            # Restaura inteiros (serializados como string no JSON)
            pacote = _json_ints(pacote)
            ok = verificar_prova_voto_valido(pacote)
            if ok:
                validas += 1
            else:
                invalidas.append({"voto_id": r["id"], "motivo": "verificacao_falhou"})
        except Exception as e:
            invalidas.append({"voto_id": r["id"], "motivo": str(e)})

    return {
        "eleicao_id": eleicao_id,
        "total_votos": total,
        "com_prova_or": com_prova,
        "provas_validas": validas,
        "provas_invalidas": invalidas,
        "sem_prova_or": sem_prova,
        "ok": len(invalidas) == 0 and (com_prova == 0 or validas == com_prova),
    }


def _json_ints(obj):
    """Converte strings numéricas de volta para int (provas serializadas)."""
    if isinstance(obj, dict):
        return {k: _json_ints(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_ints(x) for x in obj]
    if isinstance(obj, str) and obj.isdigit():
        return int(obj)
    return obj


def quadro_compromissos(eleicao_id: int) -> dict:
    """Quadro público de compromissos + raiz Merkle (não revela conteúdo dos votos)."""
    from zk_voto import construir_merkle

    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT id, compromisso, registrado_em
               FROM votos
               WHERE eleicao_id = ? AND compromisso IS NOT NULL
               ORDER BY id""",
            (eleicao_id,),
        )
        rows = [dict(r) for r in cursor.fetchall()]

    compromissos = [r["compromisso"] for r in rows]
    merkle = construir_merkle(compromissos)
    return {
        "eleicao_id": eleicao_id,
        "total": len(compromissos),
        "compromissos": [
            {
                "voto_id": r["id"],
                "compromisso": r["compromisso"],
                "registrado_em": r["registrado_em"],
            }
            for r in rows
        ],
        "merkle_raiz": merkle["raiz"],
    }


def verificar_votos_zk(eleicao_id: int) -> dict:
    """
    Verificação estilo ZK: confere compromissos, validade dos candidatos e Merkle.
    Usa a chave para abrir os votos (autoridade de apuração).
    """
    from zk_voto import verificar_apuracao, construir_merkle
    from crypto_voto import decifrar_voto

    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT id, voto_cifrado, integridade, compromisso, candidato_id, tipo_voto, peso
               FROM votos WHERE eleicao_id = ? ORDER BY id""",
            (eleicao_id,),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        cursor.execute(
            "SELECT id FROM candidatos WHERE eleicao_id = ? AND ativo = 1",
            (eleicao_id,),
        )
        candidatos_validos = {r["id"] for r in cursor.fetchall()}

    aberturas = []
    compromissos = []
    for r in rows:
        if r.get("voto_cifrado") and r.get("compromisso"):
            try:
                ab = decifrar_voto(r["voto_cifrado"], r.get("integridade"))
                aberturas.append(ab)
                compromissos.append(r["compromisso"])
            except ValueError:
                aberturas.append({"tipo": "erro", "candidato_id": None, "peso": 1.0, "nonce": ""})
                compromissos.append(r["compromisso"])
        else:
            aberturas.append({
                "tipo": r.get("tipo_voto") or "nulo",
                "candidato_id": r.get("candidato_id"),
                "peso": float(r.get("peso") or 1),
                "nonce": "",
            })
            compromissos.append(r.get("compromisso") or ("legado-" + str(r["id"])))

    rel = verificar_apuracao(aberturas, compromissos, candidatos_validos)
    rel["merkle_quadro"] = construir_merkle([c for c in compromissos if c])["raiz"]
    return rel


def _decifrar_votos_eleicao(eleicao_id: int) -> list[dict]:
    """Lê e decifra todos os votos de uma eleição (suporta votos legados em claro)."""
    from crypto_voto import decifrar_voto

    votos_decifrados = []
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT id, candidato_id, tipo_voto, peso, voto_cifrado, integridade
               FROM votos WHERE eleicao_id = ?""",
            (eleicao_id,),
        )
        for row in cursor.fetchall():
            r = dict(row)
            if r.get("voto_cifrado"):
                try:
                    dados = decifrar_voto(r["voto_cifrado"], r.get("integridade"))
                    votos_decifrados.append({
                        "id": r["id"],
                        "tipo": dados["tipo"],
                        "candidato_id": dados.get("candidato_id"),
                        "peso": float(dados.get("peso") or r.get("peso") or 1.0),
                        "cifrado": True,
                    })
                except ValueError:
                    # Voto corrompido — ignora na apuração mas conta como erro
                    votos_decifrados.append({
                        "id": r["id"],
                        "tipo": "erro",
                        "candidato_id": None,
                        "peso": float(r.get("peso") or 1.0),
                        "cifrado": True,
                        "erro": True,
                    })
            else:
                # Voto legado (antes da criptografia)
                votos_decifrados.append({
                    "id": r["id"],
                    "tipo": r.get("tipo_voto") or "nulo",
                    "candidato_id": r.get("candidato_id"),
                    "peso": float(r.get("peso") or 1.0),
                    "cifrado": False,
                })
    return votos_decifrados


def obter_resultados(eleicao_id: int) -> dict:
    """
    Calcula e retorna os resultados da eleição.
    Decifra os votos em memória para apurar (conteúdo permanece cifrado no disco).
    """
    eleicao = obter_eleicao(eleicao_id)
    if not eleicao:
        raise ValueError("Eleição não encontrada.")

    ponderado = bool(eleicao.get("voto_ponderado"))
    votos = _decifrar_votos_eleicao(eleicao_id)

    total_votos = len(votos)
    total_peso = sum(v["peso"] for v in votos)
    erros_cifra = sum(1 for v in votos if v.get("erro"))

    # Contagem por candidato
    contagem: dict[int, dict] = {}  # candidato_id -> {votos, peso}
    brancos_qtd = brancos_peso = 0.0
    nulos_qtd = nulos_peso = 0.0

    for v in votos:
        if v.get("erro"):
            nulos_qtd += 1
            nulos_peso += v["peso"]
            continue
        if v["tipo"] == "candidato" and v.get("candidato_id"):
            cid = v["candidato_id"]
            if cid not in contagem:
                contagem[cid] = {"votos": 0, "peso_votos": 0.0}
            contagem[cid]["votos"] += 1
            contagem[cid]["peso_votos"] += v["peso"]
        elif v["tipo"] == "branco":
            brancos_qtd += 1
            brancos_peso += v["peso"]
        else:
            nulos_qtd += 1
            nulos_peso += v["peso"]

    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT id, numero, nome, cargo FROM candidatos
               WHERE eleicao_id = ? AND ativo = 1 ORDER BY cargo, numero""",
            (eleicao_id,),
        )
        candidatos = []
        for row in cursor.fetchall():
            c = dict(row)
            stats = contagem.get(c["id"], {"votos": 0, "peso_votos": 0.0})
            c["votos"] = stats["votos"]
            c["peso_votos"] = stats["peso_votos"]
            candidatos.append(c)

        # Ordena por peso/votos dentro do cargo
        candidatos.sort(
            key=lambda c: (c["cargo"], -(c["peso_votos"] if ponderado else c["votos"]), c["numero"])
        )

        cursor.execute(
            """SELECT COUNT(*) as total, COALESCE(SUM(peso), 0) as peso_total
               FROM eleitores WHERE eleicao_id = ? AND ativo = 1""",
            (eleicao_id,),
        )
        row = cursor.fetchone()
        total_eleitores = row["total"]
        peso_eleitores = row["peso_total"]

        cursor.execute(
            """SELECT COUNT(*) as total, COALESCE(SUM(peso), 0) as peso_total
               FROM eleitores WHERE eleicao_id = ? AND ja_votou = 1""",
            (eleicao_id,),
        )
        row = cursor.fetchone()
        votaram = row["total"]
        peso_votaram = row["peso_total"]

    por_cargo: dict[str, list] = {}
    for c in candidatos:
        por_cargo.setdefault(c["cargo"], []).append(c)

    return {
        "ponderado": ponderado,
        "total_votos": total_votos,
        "total_peso": total_peso,
        "total_eleitores": total_eleitores,
        "peso_eleitores": peso_eleitores,
        "votaram": votaram,
        "peso_votaram": peso_votaram,
        "abstencoes": total_eleitores - votaram,
        "candidatos": candidatos,
        "por_cargo": por_cargo,
        "brancos": {"qtd": int(brancos_qtd), "peso": brancos_peso},
        "nulos": {"qtd": int(nulos_qtd), "peso": nulos_peso},
        "votos_cifrados": sum(1 for v in votos if v.get("cifrado") and not v.get("erro")),
        "erros_cifra": erros_cifra,
    }


def relatorio_votos(eleicao_id: int) -> dict:
    """
    Relatório de votos / presença.
    Lista quem já votou (nome, CPF mascarado, unidade, data) SEM revelar
    em quem votou — o sigilo do voto é preservado.
    """
    eleicao = obter_eleicao(eleicao_id)
    if not eleicao:
        raise ValueError("Eleição não encontrada.")

    with db_session() as conn:
        cursor = conn.cursor()

        cursor.execute(
            """SELECT nome, documento, unidade, bloco, peso, ja_votou, data_voto
               FROM eleitores
               WHERE eleicao_id = ? AND ativo = 1
               ORDER BY ja_votou DESC, data_voto, nome""",
            (eleicao_id,)
        )
        eleitores = []
        for row in cursor.fetchall():
            r = dict(row)
            cpf = r["documento"]
            # Mascara CPF: 123.***.***-09
            if len(cpf) == 11:
                r["cpf_mascarado"] = f"{cpf[:3]}.***.***-{cpf[9:]}"
            else:
                r["cpf_mascarado"] = "***"
            r["cpf_formatado"] = formatar_cpf(cpf)
            eleitores.append(r)

        cursor.execute(
            "SELECT COUNT(*) as qtd FROM votos WHERE eleicao_id = ?",
            (eleicao_id,)
        )
        total_votos = cursor.fetchone()["qtd"]

        votaram = [e for e in eleitores if e["ja_votou"]]
        pendentes = [e for e in eleitores if not e["ja_votou"]]

        return {
            "eleicao": eleicao,
            "total_eleitores": len(eleitores),
            "total_votos_computados": total_votos,
            "votaram": votaram,
            "pendentes": pendentes,
            "qtd_votaram": len(votaram),
            "qtd_pendentes": len(pendentes),
        }


def exportar_relatorio_votos_csv(eleicao_id: int, caminho: str | None = None) -> str:
    """Exporta relatório de presença/votos para CSV."""
    rel = relatorio_votos(eleicao_id)
    eleicao = rel["eleicao"]

    if not caminho:
        pasta = os.path.dirname(os.path.abspath(__file__))
        nome_seguro = "".join(c if c.isalnum() or c in "-_" else "_" for c in eleicao["nome"])[:40]
        caminho = os.path.join(pasta, f"relatorio_votos_{nome_seguro}_{eleicao_id}.csv")

    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["RELATÓRIO DE VOTOS / PRESENÇA"])
        writer.writerow(["Eleição", eleicao["nome"]])
        writer.writerow(["Status", eleicao["status"]])
        writer.writerow(["Total eleitores", rel["total_eleitores"]])
        writer.writerow(["Já votaram", rel["qtd_votaram"]])
        writer.writerow(["Pendentes", rel["qtd_pendentes"]])
        writer.writerow(["Votos computados", rel["total_votos_computados"]])
        writer.writerow([])
        writer.writerow(["OBS: Este relatório NÃO revela em quem cada pessoa votou (sigilo)."])
        writer.writerow([])
        writer.writerow(["STATUS", "NOME", "CPF", "UNIDADE", "BLOCO", "PESO", "DATA DO VOTO"])

        for e in rel["votaram"]:
            writer.writerow([
                "VOTOU", e["nome"], e["cpf_formatado"],
                e["unidade"] or "", e["bloco"] or "",
                f"{e['peso']:.2f}", e["data_voto"] or ""
            ])
        for e in rel["pendentes"]:
            writer.writerow([
                "PENDENTE", e["nome"], e["cpf_formatado"],
                e["unidade"] or "", e["bloco"] or "",
                f"{e['peso']:.2f}", ""
            ])

    return caminho


# ==================== EXPORTAÇÃO ====================

def exportar_resultados_csv(eleicao_id: int, caminho: str | None = None) -> str:
    """Exporta resultados para arquivo CSV. Retorna o caminho do arquivo."""
    eleicao = obter_eleicao(eleicao_id)
    if not eleicao:
        raise ValueError("Eleição não encontrada.")

    resultados = obter_resultados(eleicao_id)

    if not caminho:
        pasta = os.path.dirname(os.path.abspath(__file__))
        nome_seguro = "".join(c if c.isalnum() or c in "-_" else "_" for c in eleicao["nome"])[:40]
        caminho = os.path.join(pasta, f"resultados_{nome_seguro}_{eleicao_id}.csv")

    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["RESULTADOS DA ELEIÇÃO"])
        writer.writerow(["Nome", eleicao["nome"]])
        writer.writerow(["Tipo", eleicao["tipo"]])
        writer.writerow(["Status", eleicao["status"]])
        writer.writerow(["Voto Ponderado", "Sim" if resultados["ponderado"] else "Não"])
        writer.writerow([])
        writer.writerow(["RESUMO"])
        writer.writerow(["Total de eleitores aptos", resultados["total_eleitores"]])
        writer.writerow(["Compareceram", resultados["votaram"]])
        writer.writerow(["Abstenções", resultados["abstencoes"]])
        writer.writerow(["Total de votos computados", resultados["total_votos"]])
        if resultados["ponderado"]:
            writer.writerow(["Peso total dos votos", f"{resultados['total_peso']:.2f}"])
            writer.writerow(["Peso dos eleitores aptos", f"{resultados['peso_eleitores']:.2f}"])
        writer.writerow([])
        writer.writerow(["CARGO", "NÚMERO", "NOME", "VOTOS", "PESO", "PERCENTUAL"])

        for cargo, lista in resultados["por_cargo"].items():
            total_cargo = sum(c["peso_votos"] for c in lista) if resultados["ponderado"] else sum(c["votos"] for c in lista)
            for c in lista:
                valor = c["peso_votos"] if resultados["ponderado"] else c["votos"]
                pct = (valor / total_cargo * 100) if total_cargo > 0 else 0
                writer.writerow([
                    cargo, c["numero"], c["nome"], c["votos"],
                    f"{c['peso_votos']:.2f}", f"{pct:.1f}%"
                ])

        writer.writerow([])
        writer.writerow(["Votos em BRANCO", resultados["brancos"]["qtd"], f"{resultados['brancos']['peso']:.2f}"])
        writer.writerow(["Votos NULOS", resultados["nulos"]["qtd"], f"{resultados['nulos']['peso']:.2f}"])

    return caminho


def exportar_resultados_pdf(eleicao_id: int, caminho: str | None = None) -> str:
    """Exporta resultados para arquivo PDF. Retorna o caminho do arquivo."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    from reportlab.lib.enums import TA_CENTER

    eleicao = obter_eleicao(eleicao_id)
    if not eleicao:
        raise ValueError("Eleição não encontrada.")

    resultados = obter_resultados(eleicao_id)

    if not caminho:
        pasta = os.path.dirname(os.path.abspath(__file__))
        nome_seguro = "".join(c if c.isalnum() or c in "-_" else "_" for c in eleicao["nome"])[:40]
        caminho = os.path.join(pasta, f"resultados_{nome_seguro}_{eleicao_id}.pdf")

    doc = SimpleDocTemplate(
        caminho, pagesize=A4,
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Titulo", parent=styles["Heading1"], alignment=TA_CENTER, fontSize=16, spaceAfter=6))
    styles.add(ParagraphStyle(name="Subtitulo", parent=styles["Normal"], alignment=TA_CENTER, fontSize=11, textColor=colors.grey, spaceAfter=12))
    styles.add(ParagraphStyle(name="Secao", parent=styles["Heading2"], fontSize=12, spaceBefore=12, spaceAfter=6))
    styles.add(ParagraphStyle(name="Normal2", parent=styles["Normal"], fontSize=10, spaceAfter=3))

    elementos = []

    elementos.append(Paragraph("URNA ELETRÔNICA — BOLETIM DE RESULTADOS", styles["Titulo"]))
    elementos.append(Paragraph(eleicao["nome"], styles["Subtitulo"]))
    elementos.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#333333")))
    elementos.append(Spacer(1, 8))

    tipo_label = {"condominio": "Condomínio", "associacao": "Associação", "sindicato": "Sindicato"}.get(eleicao["tipo"], eleicao["tipo"])
    elementos.append(Paragraph(f"<b>Tipo:</b> {tipo_label} &nbsp;&nbsp; <b>Status:</b> {eleicao['status'].upper()}", styles["Normal2"]))
    elementos.append(Paragraph(f"<b>Voto ponderado:</b> {'Sim' if resultados['ponderado'] else 'Não'}", styles["Normal2"]))
    if eleicao.get("data_abertura"):
        elementos.append(Paragraph(f"<b>Abertura:</b> {eleicao['data_abertura']}", styles["Normal2"]))
    if eleicao.get("data_fechamento"):
        elementos.append(Paragraph(f"<b>Fechamento:</b> {eleicao['data_fechamento']}", styles["Normal2"]))

    elementos.append(Paragraph("Resumo da Apuração", styles["Secao"]))

    dados_resumo = [
        ["Total de eleitores aptos", str(resultados["total_eleitores"])],
        ["Compareceram (votaram)", str(resultados["votaram"])],
        ["Abstenções", str(resultados["abstencoes"])],
        ["Total de votos computados", str(resultados["total_votos"])],
    ]
    if resultados["ponderado"]:
        dados_resumo.append(["Peso total dos votos", f"{resultados['total_peso']:.2f}"])
        dados_resumo.append(["Peso dos eleitores aptos", f"{resultados['peso_eleitores']:.2f}"])

    tabela_resumo = Table(dados_resumo, colWidths=[10*cm, 5*cm])
    tabela_resumo.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f5f5f5")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("PADDING", (0, 0), (-1, -1), 6),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
    ]))
    elementos.append(tabela_resumo)

    for cargo, lista in resultados["por_cargo"].items():
        elementos.append(Paragraph(f"Cargo: {cargo}", styles["Secao"]))

        total_cargo = sum(c["peso_votos"] for c in lista) if resultados["ponderado"] else sum(c["votos"] for c in lista)

        cabecalho = ["Pos.", "Nº", "Nome", "Votos"]
        if resultados["ponderado"]:
            cabecalho.append("Peso")
        cabecalho.append("%")

        dados = [cabecalho]
        for i, c in enumerate(lista, 1):
            valor = c["peso_votos"] if resultados["ponderado"] else c["votos"]
            pct = (valor / total_cargo * 100) if total_cargo > 0 else 0
            linha = [str(i), str(c["numero"]), c["nome"], str(c["votos"])]
            if resultados["ponderado"]:
                linha.append(f"{c['peso_votos']:.2f}")
            linha.append(f"{pct:.1f}%")
            dados.append(linha)

        col_widths = [1.2*cm, 1.5*cm, 7*cm, 2*cm]
        if resultados["ponderado"]:
            col_widths.append(2*cm)
        col_widths.append(1.8*cm)

        tabela = Table(dados, colWidths=col_widths)
        tabela.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#d5f5e3")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bbbbbb")),
            ("PADDING", (0, 0), (-1, -1), 5),
            ("ALIGN", (0, 0), (1, -1), "CENTER"),
            ("ALIGN", (-2, 0), (-1, -1), "CENTER"),
        ]))
        elementos.append(tabela)

    elementos.append(Spacer(1, 12))
    elementos.append(Paragraph(
        f"<b>Votos em BRANCO:</b> {resultados['brancos']['qtd']} "
        f"(peso {resultados['brancos']['peso']:.2f}) &nbsp;&nbsp; "
        f"<b>Votos NULOS:</b> {resultados['nulos']['qtd']} "
        f"(peso {resultados['nulos']['peso']:.2f})",
        styles["Normal2"]
    ))

    elementos.append(Spacer(1, 20))
    elementos.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
    elementos.append(Paragraph(
        f"Documento gerado em {datetime.now().strftime('%d/%m/%Y às %H:%M:%S')} — Sistema de Urna Eletrônica",
        ParagraphStyle(name="Rodape", parent=styles["Normal"], fontSize=8, textColor=colors.grey, alignment=TA_CENTER)
    ))

    doc.build(elementos)
    return caminho
