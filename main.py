#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║     SISTEMA DE URNA ELETRÔNICA                               ║
║     Para Condomínios • Associações • Sindicatos              ║
╚══════════════════════════════════════════════════════════════╝

Versão: 4.0 — voto por cargo, registro atômico e administração completa
"""

import getpass
import os
import sys
from datetime import datetime

from database import (
    inicializar_banco, verificar_admin, alterar_senha_admin,
    fazer_backup, listar_backups, restaurar_backup, inspecionar_banco, DB_PATH,
    consultar_auditoria, listar_acoes_auditoria, auditoria_consistencia_votos,
    exportar_auditoria_csv, criar_admin, listar_admins, desativar_admin,
    redefinir_senha_admin,
)
from eleicao import (
    ErroApuracao,
    criar_eleicao, listar_eleicoes, obter_eleicao, alterar_status_eleicao,
    atualizar_eleicao, excluir_eleicao,
    cadastrar_candidato, listar_candidatos, obter_candidato_por_numero,
    obter_candidato, atualizar_candidato, excluir_candidato,
    cadastrar_eleitor, listar_eleitores, autenticar_eleitor, obter_eleitor,
    atualizar_eleitor, excluir_eleitor, importar_eleitores_csv, modelo_csv_eleitores,
    registrar_voto, obter_resultados, criar_eleicao_demonstracao,
    exportar_resultados_csv, exportar_resultados_pdf, listar_cargos,
    relatorio_votos, exportar_relatorio_votos_csv, formatar_cpf,
    obter_recibo_voto, quadro_compromissos, verificar_votos_zk,
    verificar_provas_or, obter_resultados_homo, verificar_recibo,
    exportar_quadro_compromissos,
)


def limpar_tela():
    os.system("cls" if os.name == "nt" else "clear")


def pausar():
    input("\n  Pressione ENTER para continuar...")


def titulo(texto: str):
    print("\n" + "═" * 62)
    print(f"  {texto}")
    print("═" * 62)


def ok(msg: str):
    print(f"\n  ✓ {msg}")


def erro(msg: str):
    print(f"\n  ✗ {msg}")


def aviso(msg: str):
    print(f"\n  ⚠ {msg}")


def perguntar_sim(pergunta: str) -> bool:
    return input(f"  {pergunta} (s/N): ").strip().lower() == "s"


def ler_int(prompt: str, padrao: int | None = None) -> int | None:
    txt = input(prompt).strip()
    if not txt:
        return padrao
    try:
        return int(txt)
    except ValueError:
        erro("Número inválido.")
        return None


# ==================== MENU PRINCIPAL ====================

def menu_principal():
    while True:
        limpar_tela()
        print("""
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║              URNA ELETRÔNICA  v4.0                           ║
║     Condomínios • Associações • Sindicatos                   ║
║                                                              ║
║         Um voto por cargo, sigiloso e verificável            ║
╚══════════════════════════════════════════════════════════════╝

  [1] Área do Administrador
  [2] Área de Votação (Eleitor)
  [3] Criar Eleição de Demonstração (para testar)
  [4] Conferir meu recibo de voto
  [0] Sair
""")
        opcao = input("  Escolha uma opção: ").strip()

        if opcao == "1":
            login_admin()
        elif opcao == "2":
            area_votacao()
        elif opcao == "3":
            criar_demo()
        elif opcao == "4":
            menu_conferir_recibo()
        elif opcao == "0":
            limpar_tela()
            print("\n  Obrigado por usar a Urna Eletrônica!\n")
            sys.exit(0)
        else:
            erro("Opção inválida!")
            pausar()


def criar_demo():
    limpar_tela()
    titulo("CRIAR ELEIÇÃO DE DEMONSTRAÇÃO")
    print("""
  Isso cria automaticamente:

  • 1 eleição de condomínio com voto ponderado
  • 4 candidatos em 2 cargos (Síndico e Conselheiro)
  • 6 eleitores (identificação pelo CPF)

  Cada eleitor vota UMA vez em CADA cargo.

  CPFs válidos (algoritmo oficial) dos eleitores de demonstração:
    123.456.789-09  |  234.567.890-92  |  345.678.901-75
    456.789.012-49  |  567.890.123-03  |  678.901.234-69
""")
    if not perguntar_sim("Confirma criação?"):
        return
    try:
        eid = criar_eleicao_demonstracao()
        ok(f"Eleição de demonstração criada! ID: {eid}")
        print("\n  Agora faça login como administrador, abra a votação")
        print("  e teste na Área de Votação digitando um dos CPFs acima.")
    except Exception as e:
        erro(f"Erro: {e}")
    pausar()


def menu_conferir_recibo():
    """Qualquer eleitor pode conferir se o voto dele entrou no quadro público."""
    limpar_tela()
    titulo("CONFERIR RECIBO DE VOTO")
    print("""
  Ao votar você recebeu um código (compromisso) por cargo.
  Aqui você confere se ele consta no quadro público da eleição.
  A conferência NÃO revela em quem você votou.
""")
    eleicao = selecionar_eleicao("Selecione a eleição do seu voto")
    if not eleicao:
        return
    comp = input("\n  Cole o recibo: ").strip()
    if not comp:
        return
    try:
        v = verificar_recibo(eleicao["id"], comp)
    except Exception as e:
        erro(str(e))
        pausar()
        return

    if v["encontrado"] and v["prova_valida"]:
        ok(v["mensagem"])
        print(f"    Posição no quadro: {v['indice']} de {v['total_no_quadro']}")
        print(f"    Cargo: {v.get('cargo') or '—'}")
        print(f"    Registrado em: {v.get('registrado_em')}")
        print(f"\n    Raiz Merkle: {v['merkle_raiz']}")
        print("\n  A prova de inclusão Merkle confere: seu voto está na urna")
        print("  e o quadro não foi alterado depois.")
    else:
        erro(v["mensagem"])
    pausar()


# ==================== ADMINISTRAÇÃO ====================

def login_admin():
    limpar_tela()
    titulo("LOGIN DO ADMINISTRADOR")

    usuario = input("\n  Usuário: ").strip()
    try:
        senha = getpass.getpass("  Senha: ")
    except (EOFError, KeyboardInterrupt):
        return

    try:
        admin = verificar_admin(usuario, senha)
    except PermissionError as e:
        erro(str(e))
        pausar()
        return

    if not admin:
        erro("Usuário ou senha incorretos!")
        pausar()
        return

    if admin.get("trocar_senha"):
        limpar_tela()
        titulo("TROCA DE SENHA OBRIGATÓRIA")
        print("\n  Este usuário ainda usa a senha inicial.")
        print("  Defina uma senha própria para continuar.\n")
        if not trocar_senha(admin, senha_atual_conhecida=senha):
            aviso("Senha não alterada. Acesso interrompido.")
            pausar()
            return

    menu_admin(admin)


def menu_admin(admin: dict):
    while True:
        limpar_tela()
        titulo(f"PAINEL ADMINISTRATIVO — {admin['nome']}")
        print("""
  [1] Gerenciar Eleições (criar / editar / excluir)
  [2] Gerenciar Candidatos
  [3] Gerenciar Eleitores (inclui importar CSV)
  [4] Abrir / Fechar Votação
  [5] Ver Resultados
  [6] Exportar Resultados (CSV / PDF / quadro público)
  [7] Listar Eleitores (quem já votou)
  [8] Relatório de Votos (presença)
  [9] Banco de Dados (backup / restauração / inspeção)
  [B] Auditoria de Votos / Log
  [C] Administradores do sistema
  [A] Trocar minha senha
  [0] Voltar ao Menu Principal
""")
        opcao = input("  Escolha uma opção: ").strip().upper()

        if opcao == "1":
            gerenciar_eleicoes(admin)
        elif opcao == "2":
            menu_gerenciar_candidatos(admin)
        elif opcao == "3":
            menu_gerenciar_eleitores(admin)
        elif opcao == "4":
            menu_status_votacao(admin)
        elif opcao == "5":
            menu_resultados()
        elif opcao == "6":
            menu_exportar()
        elif opcao == "7":
            menu_listar_eleitores()
        elif opcao == "8":
            menu_relatorio_votos()
        elif opcao == "9":
            menu_banco_dados()
        elif opcao == "B":
            menu_auditoria()
        elif opcao == "C":
            menu_administradores(admin)
        elif opcao == "A":
            trocar_senha(admin)
        elif opcao == "0":
            return
        else:
            erro("Opção inválida!")
            pausar()


def selecionar_eleicao(mensagem: str = "Selecione a eleição") -> dict | None:
    eleicoes = listar_eleicoes()
    if not eleicoes:
        aviso("Nenhuma eleição cadastrada.")
        pausar()
        return None

    print(f"\n  {mensagem}:\n")
    for e in eleicoes:
        icon = {"preparacao": "🔧", "aberta": "🟢", "fechada": "🔴"}.get(e["status"], "?")
        pond = " [PONDERADO]" if e.get("voto_ponderado") else ""
        print(f"  [{e['id']}] {icon} {e['nome']} ({e['tipo']}) — {e['status'].upper()}{pond}")

    eid = ler_int("\n  Digite o ID da eleição (0 para cancelar): ")
    if eid is None:
        pausar()
        return None
    if eid == 0:
        return None
    eleicao = obter_eleicao(eid)
    if not eleicao:
        erro("Eleição não encontrada.")
        pausar()
        return None
    return eleicao


# ---------- eleições ----------

def gerenciar_eleicoes(admin: dict):
    while True:
        limpar_tela()
        titulo("GERENCIAR ELEIÇÕES")

        eleicoes = listar_eleicoes()
        if eleicoes:
            print("\n  Eleições cadastradas:\n")
            for e in eleicoes:
                icon = {"preparacao": "🔧", "aberta": "🟢", "fechada": "🔴"}.get(e["status"], "?")
                pond = " | Voto ponderado" if e.get("voto_ponderado") else ""
                cargos = listar_cargos(e["id"])
                print(f"  [{e['id']}] {icon} {e['nome']}")
                print(f"       Tipo: {e['tipo'].capitalize()} | Status: {e['status'].upper()}{pond}")
                print(f"       Cargos: {', '.join(cargos) if cargos else '(nenhum)'}")
                if e["descricao"]:
                    print(f"       {e['descricao']}")
                print()
        else:
            print("\n  Nenhuma eleição cadastrada ainda.\n")

        print("  [1] Criar nova eleição")
        print("  [2] Editar uma eleição")
        print("  [3] Excluir uma eleição")
        print("  [0] Voltar")
        opcao = input("\n  Opção: ").strip()

        if opcao == "1":
            criar_nova_eleicao()
        elif opcao == "2":
            editar_eleicao(admin)
        elif opcao == "3":
            remover_eleicao(admin)
        elif opcao == "0":
            return


def criar_nova_eleicao():
    limpar_tela()
    titulo("CRIAR NOVA ELEIÇÃO")

    nome = input("\n  Nome da eleição: ").strip()
    if not nome:
        erro("Nome é obrigatório!")
        pausar()
        return

    descricao = input("  Descrição (opcional): ").strip()

    print("\n  Tipo de organização:")
    print("  [1] Condomínio")
    print("  [2] Associação")
    print("  [3] Sindicato")
    tipos = {"1": "condominio", "2": "associacao", "3": "sindicato"}
    tipo = tipos.get(input("  Escolha: ").strip())
    if not tipo:
        erro("Tipo inválido!")
        pausar()
        return

    print("\n  Usar voto ponderado? (ex: fração ideal no condomínio)")
    print("  [1] Sim — cada eleitor tem um peso")
    print("  [2] Não — um eleitor = um voto")
    voto_ponderado = (input("  Escolha [2]: ").strip() or "2") == "1"

    try:
        eid = criar_eleicao(nome, descricao, tipo, voto_ponderado)
        ok(f"Eleição criada com sucesso! ID: {eid}")
        print("  Próximo passo: cadastre os candidatos (cada cargo vira uma")
        print("  cédula própria) e depois os eleitores.")
        if voto_ponderado:
            print("  Lembre-se de informar o peso de cada eleitor no cadastro.")
    except Exception as e:
        erro(f"Erro: {e}")
    pausar()


def editar_eleicao(admin: dict):
    eleicao = selecionar_eleicao("Selecione a eleição para editar")
    if not eleicao:
        return
    limpar_tela()
    titulo(f"EDITAR ELEIÇÃO — {eleicao['nome']}")
    print("\n  Deixe em branco para manter o valor atual.\n")

    nome = input(f"  Nome [{eleicao['nome']}]: ").strip() or None
    descricao = input(f"  Descrição [{eleicao['descricao'] or ''}]: ").strip()
    descricao = descricao if descricao else None

    pond_atual = "Sim" if eleicao["voto_ponderado"] else "Não"
    pond_txt = input(f"  Voto ponderado? (s/n) [{pond_atual}]: ").strip().lower()
    voto_ponderado = None
    if pond_txt in ("s", "n"):
        voto_ponderado = pond_txt == "s"

    try:
        if atualizar_eleicao(eleicao["id"], nome, descricao, voto_ponderado,
                             autor=admin["usuario"]):
            ok("Eleição atualizada.")
        else:
            aviso("Nada foi alterado.")
    except ValueError as e:
        erro(str(e))
    pausar()


def remover_eleicao(admin: dict):
    eleicao = selecionar_eleicao("Selecione a eleição para EXCLUIR")
    if not eleicao:
        return
    limpar_tela()
    titulo("EXCLUIR ELEIÇÃO")
    print(f"\n  Eleição: {eleicao['nome']}")
    print(f"  Candidatos: {len(listar_candidatos(eleicao['id']))}")
    print(f"  Eleitores:  {len(listar_eleitores(eleicao['id']))}")
    print("\n  Isso apaga candidatos e eleitores dessa eleição.")
    print("  Um backup do banco é feito automaticamente antes.")

    confirmacao = input("\n  Digite o nome exato da eleição para confirmar: ").strip()
    if confirmacao != eleicao["nome"]:
        aviso("Nome não confere. Exclusão cancelada.")
        pausar()
        return

    try:
        excluir_eleicao(eleicao["id"], autor=admin["usuario"])
        ok("Eleição excluída.")
    except ValueError as e:
        erro(str(e))
    pausar()


# ---------- candidatos ----------

def menu_gerenciar_candidatos(admin: dict):
    eleicao = selecionar_eleicao("Selecione a eleição para gerenciar candidatos")
    if not eleicao:
        return

    while True:
        eleicao = obter_eleicao(eleicao["id"])
        limpar_tela()
        titulo(f"CANDIDATOS — {eleicao['nome']}")
        print(f"  Status da eleição: {eleicao['status'].upper()}")
        if eleicao["status"] != "preparacao":
            aviso("Fora da preparação só é possível desativar candidatos.")

        candidatos = listar_candidatos(eleicao["id"], apenas_ativos=False)
        if candidatos:
            print("\n  Candidatos:\n")
            cargo_atual = None
            for c in candidatos:
                if c["cargo"] != cargo_atual:
                    cargo_atual = c["cargo"]
                    print(f"  ── {cargo_atual} ──")
                marca = "" if c["ativo"] else "  (INATIVO)"
                print(f"     [{c['id']}] Nº {c['numero']:02d} — {c['nome']}{marca}")
                if c.get("descricao"):
                    print(f"            {c['descricao']}")
        else:
            print("\n  Nenhum candidato cadastrado ainda.\n")

        print("\n  [1] Cadastrar novo candidato")
        print("  [2] Editar candidato")
        print("  [3] Excluir / desativar candidato")
        print("  [0] Voltar")
        opcao = input("\n  Opção: ").strip()

        if opcao == "1":
            _cadastrar_candidato_interativo(eleicao)
        elif opcao == "2":
            _editar_candidato_interativo(admin)
        elif opcao == "3":
            _excluir_candidato_interativo(admin)
        elif opcao == "0":
            return


def _cadastrar_candidato_interativo(eleicao: dict):
    if eleicao["status"] != "preparacao":
        erro("Só é possível cadastrar candidatos em eleições em preparação.")
        pausar()
        return
    try:
        numero = ler_int("\n  Número do candidato: ")
        if numero is None:
            pausar()
            return
        nome = input("  Nome completo: ").strip()
        cargos_existentes = listar_cargos(eleicao["id"])
        if cargos_existentes:
            print(f"  Cargos já criados: {', '.join(cargos_existentes)}")
        cargo = input("  Cargo pretendido (ex: Síndico, Conselheiro): ").strip()
        descricao = input("  Descrição/proposta (opcional): ").strip()

        cid = cadastrar_candidato(eleicao["id"], numero, nome, cargo, descricao)
        ok(f"Candidato cadastrado! ID: {cid}")
    except ValueError as e:
        erro(str(e))
    except Exception as e:
        erro(f"Erro inesperado: {e}")
    pausar()


def _editar_candidato_interativo(admin: dict):
    cid = ler_int("\n  ID do candidato a editar: ")
    if cid is None:
        pausar()
        return
    cand = obter_candidato(cid)
    if not cand:
        erro("Candidato não encontrado.")
        pausar()
        return

    print("\n  Deixe em branco para manter.\n")
    numero = input(f"  Número [{cand['numero']}]: ").strip()
    nome = input(f"  Nome [{cand['nome']}]: ").strip()
    cargo = input(f"  Cargo [{cand['cargo']}]: ").strip()
    descricao = input(f"  Descrição [{cand['descricao'] or ''}]: ").strip()

    try:
        alterado = atualizar_candidato(
            cid,
            numero=int(numero) if numero else None,
            nome=nome or None,
            cargo=cargo or None,
            descricao=descricao if descricao else None,
            autor=admin["usuario"],
        )
        ok("Candidato atualizado.") if alterado else aviso("Nada foi alterado.")
    except ValueError as e:
        erro(str(e))
    pausar()


def _excluir_candidato_interativo(admin: dict):
    cid = ler_int("\n  ID do candidato a remover: ")
    if cid is None:
        pausar()
        return
    cand = obter_candidato(cid)
    if not cand:
        erro("Candidato não encontrado.")
        pausar()
        return
    print(f"\n  {cand['nome']} (Nº {cand['numero']}, {cand['cargo']})")
    if not perguntar_sim("Confirma a remoção?"):
        return
    try:
        excluir_candidato(cid, autor=admin["usuario"])
        ok("Candidato removido/desativado.")
    except ValueError as e:
        erro(str(e))
    pausar()


# ---------- eleitores ----------

def menu_gerenciar_eleitores(admin: dict):
    eleicao = selecionar_eleicao("Selecione a eleição para gerenciar eleitores")
    if not eleicao:
        return

    while True:
        eleicao = obter_eleicao(eleicao["id"])
        limpar_tela()
        titulo(f"ELEITORES — {eleicao['nome']}")

        eleitores = listar_eleitores(eleicao["id"])
        cargos = listar_cargos(eleicao["id"])
        print(f"\n  Total de eleitores: {len(eleitores)}")
        print(f"  Cargos nesta eleição: {', '.join(cargos) if cargos else '(nenhum)'}")
        if eleicao.get("voto_ponderado"):
            print("  (Esta eleição usa voto ponderado — informe o peso de cada um)")
        print()

        print("  [1] Cadastrar um eleitor")
        print("  [2] Cadastrar vários de uma vez (digitação em lote)")
        print("  [3] Importar de arquivo CSV")
        print("  [4] Gerar modelo de CSV")
        print("  [5] Editar eleitor")
        print("  [6] Excluir eleitor")
        print("  [0] Voltar")
        opcao = input("\n  Opção: ").strip()

        if opcao == "1":
            cadastrar_um_eleitor(eleicao)
        elif opcao == "2":
            cadastrar_lote_eleitores(eleicao)
        elif opcao == "3":
            importar_csv_eleitores(eleicao)
        elif opcao == "4":
            try:
                caminho = modelo_csv_eleitores()
                ok(f"Modelo gerado em:\n     {caminho}")
            except Exception as e:
                erro(str(e))
            pausar()
        elif opcao == "5":
            _editar_eleitor_interativo(eleicao, admin)
        elif opcao == "6":
            _excluir_eleitor_interativo(eleicao, admin)
        elif opcao == "0":
            return


def cadastrar_um_eleitor(eleicao: dict):
    print("\n  --- Novo Eleitor ---")
    print("  (A identificação e o controle de voto são feitos pelo CPF)\n")
    nome = input("  Nome completo: ").strip()
    cpf = input("  CPF (ex: 123.456.789-00): ").strip()

    unidade = ""
    bloco = ""
    if eleicao["tipo"] == "condominio":
        unidade = input("  Unidade/Apartamento: ").strip()
        bloco = input("  Bloco (opcional): ").strip()
    elif eleicao["tipo"] in ("associacao", "sindicato"):
        unidade = input("  Número de matrícula/associado (opcional): ").strip()

    peso = 1.0
    if eleicao.get("voto_ponderado"):
        try:
            peso_str = input("  Peso do voto (ex: 1.0, 1.5, 2.0) [1.0]: ").strip() or "1.0"
            peso = float(peso_str.replace(",", "."))
        except ValueError:
            erro("Peso inválido. Usando 1.0")
            peso = 1.0

    try:
        eid = cadastrar_eleitor(eleicao["id"], nome, cpf, unidade=unidade, bloco=bloco, peso=peso)
        ok(f"Eleitor cadastrado! ID: {eid}")
        print(f"  CPF: {formatar_cpf(cpf)}")
        print("  Na votação, o eleitor se identifica com este CPF.")
        if eleicao.get("voto_ponderado"):
            print(f"  Peso: {peso}")
    except ValueError as e:
        erro(str(e))
    pausar()


def cadastrar_lote_eleitores(eleicao: dict):
    print("\n  --- Cadastro em Lote ---")
    print("  A identificação e o controle de voto são feitos pelo CPF.\n")
    if eleicao.get("voto_ponderado"):
        print("  Formato: Nome | CPF | Unidade | Bloco | Peso")
    else:
        print("  Formato: Nome | CPF | Unidade | Bloco")
    print("  (deixe linha em branco para finalizar)\n")

    contador = 0
    while True:
        linha = input("  > ").strip()
        if not linha:
            break
        partes = [p.strip() for p in linha.split("|")]
        if len(partes) < 2:
            print("    Formato inválido. Use: Nome | CPF | ...")
            continue
        nome, cpf = partes[0], partes[1]
        unidade = partes[2] if len(partes) > 2 else ""
        bloco = partes[3] if len(partes) > 3 else ""
        peso = 1.0
        if eleicao.get("voto_ponderado") and len(partes) > 4:
            try:
                peso = float(partes[4].replace(",", "."))
            except ValueError:
                peso = 1.0
        try:
            cadastrar_eleitor(eleicao["id"], nome, cpf, unidade=unidade, bloco=bloco, peso=peso)
            contador += 1
            print(f"    ✓ {nome} (CPF {formatar_cpf(cpf)})")
        except ValueError as e:
            print(f"    ✗ {nome}: {e}")

    print(f"\n  Total cadastrados neste lote: {contador}")
    pausar()


def importar_csv_eleitores(eleicao: dict):
    print("\n  --- Importar eleitores de CSV ---")
    print("  Colunas aceitas: nome; cpf; unidade; bloco; peso")
    print("  (use a opção [4] para gerar um modelo)\n")
    caminho = input("  Caminho do arquivo CSV: ").strip().strip('"')
    if not caminho:
        return
    try:
        rel = importar_eleitores_csv(eleicao["id"], caminho)
    except (FileNotFoundError, ValueError) as e:
        erro(str(e))
        pausar()
        return

    ok(f"{rel['importados']} eleitor(es) importado(s).")
    if rel["erros"]:
        aviso(f"{rel['total_erros']} linha(s) com problema:")
        for e in rel["erros"][:20]:
            print(f"    linha {e['linha']} ({e['nome'] or '?'}): {e['erro']}")
        if rel["total_erros"] > 20:
            print(f"    ... e mais {rel['total_erros'] - 20}")
    pausar()


def _editar_eleitor_interativo(eleicao: dict, admin: dict):
    eid = ler_int("\n  ID do eleitor a editar: ")
    if eid is None:
        pausar()
        return
    eleitor = obter_eleitor(eid)
    if not eleitor or eleitor["eleicao_id"] != eleicao["id"]:
        erro("Eleitor não encontrado nesta eleição.")
        pausar()
        return

    print("\n  Deixe em branco para manter.")
    print("  CPF e peso ficam travados depois que a pessoa vota.\n")
    nome = input(f"  Nome [{eleitor['nome']}]: ").strip()
    cpf = input(f"  CPF [{formatar_cpf(eleitor['documento'])}]: ").strip()
    unidade = input(f"  Unidade [{eleitor['unidade'] or ''}]: ").strip()
    bloco = input(f"  Bloco [{eleitor['bloco'] or ''}]: ").strip()
    peso_txt = input(f"  Peso [{eleitor['peso']}]: ").strip()

    peso = None
    if peso_txt:
        try:
            peso = float(peso_txt.replace(",", "."))
        except ValueError:
            erro("Peso inválido.")
            pausar()
            return

    try:
        alterado = atualizar_eleitor(
            eid,
            nome=nome or None,
            documento=cpf or None,
            unidade=unidade if unidade else None,
            bloco=bloco if bloco else None,
            peso=peso,
            autor=admin["usuario"],
        )
        ok("Eleitor atualizado.") if alterado else aviso("Nada foi alterado.")
    except ValueError as e:
        erro(str(e))
    pausar()


def _excluir_eleitor_interativo(eleicao: dict, admin: dict):
    eid = ler_int("\n  ID do eleitor a excluir: ")
    if eid is None:
        pausar()
        return
    eleitor = obter_eleitor(eid)
    if not eleitor or eleitor["eleicao_id"] != eleicao["id"]:
        erro("Eleitor não encontrado nesta eleição.")
        pausar()
        return
    print(f"\n  {eleitor['nome']} — CPF {formatar_cpf(eleitor['documento'])}")
    if not perguntar_sim("Confirma a exclusão?"):
        return
    try:
        excluir_eleitor(eid, autor=admin["usuario"])
        ok("Eleitor excluído.")
    except ValueError as e:
        erro(str(e))
    pausar()


# ---------- status ----------

def menu_status_votacao(admin: dict):
    eleicao = selecionar_eleicao("Selecione a eleição para alterar status")
    if not eleicao:
        return

    limpar_tela()
    titulo(f"STATUS DA ELEIÇÃO — {eleicao['nome']}")
    print(f"\n  Status atual: {eleicao['status'].upper()}")
    print(f"  Cargos: {', '.join(listar_cargos(eleicao['id'])) or '(nenhum)'}\n")

    print("  [1] Colocar em PREPARAÇÃO")
    print("  [2] ABRIR votação")
    print("  [3] FECHAR votação")
    print("  [0] Cancelar")
    opcao = input("\n  Opção: ").strip()

    mapa = {"1": "preparacao", "2": "aberta", "3": "fechada"}
    novo = mapa.get(opcao)
    if not novo:
        return

    if novo == "aberta":
        candidatos = listar_candidatos(eleicao["id"])
        eleitores = listar_eleitores(eleicao["id"])
        cargos = listar_cargos(eleicao["id"])
        if not candidatos:
            erro("Não é possível abrir sem candidatos cadastrados!")
            pausar()
            return
        if not eleitores:
            erro("Não é possível abrir sem eleitores cadastrados!")
            pausar()
            return
        print(f"\n  Candidatos: {len(candidatos)} | Eleitores: {len(eleitores)}")
        print(f"  Cargos: {', '.join(cargos)}")
        print(f"  Cada eleitor emitirá {len(cargos)} voto(s).")
        if not eleicao.get("contadores_homo"):
            print("\n  Inicializando contadores homomórficos (pode levar alguns")
            print("  segundos na primeira vez, por causa da geração da chave).")
        if not perguntar_sim("Confirma abertura da votação?"):
            return

    if novo == "fechada":
        if not perguntar_sim("Confirma FECHAMENTO da votação?"):
            return

    try:
        # Backup automático antes de abrir ou fechar
        if novo in ("aberta", "fechada"):
            try:
                bk = fazer_backup(f"antes_{novo}")
                print(f"\n  Backup automático: {os.path.basename(bk)}")
            except Exception as e:
                aviso(f"Backup não realizado: {e}")
        alterar_status_eleicao(eleicao["id"], novo, autor=admin["usuario"])
        ok(f"Status alterado para: {novo.upper()}")
    except Exception as e:
        erro(str(e))
    pausar()


# ---------- resultados ----------

def _obter_resultados_ou_avisar(eleicao_id: int) -> dict | None:
    """Apura em modo estrito; se falhar, explica e oferece o modo parcial."""
    try:
        return obter_resultados(eleicao_id)
    except ErroApuracao as e:
        erro(str(e))
        print("\n  Votos que não puderam ser abertos:")
        for f in e.detalhes[:10]:
            print(f"    voto #{f.get('voto_id')} ({f.get('cargo')}): {f.get('erro')}")
        if len(e.detalhes) > 10:
            print(f"    ... e mais {len(e.detalhes) - 10}")
        print("""
  Causas comuns:
    • O arquivo urna.key foi perdido, trocado ou é de outra urna
    • O banco foi restaurado sem a chave correspondente
    • Corrupção de dados

  A apuração foi bloqueada de propósito: contar esses votos como nulos
  produziria um resultado ERRADO e silencioso.
""")
        if perguntar_sim("Ver mesmo assim, ignorando os votos ilegíveis?"):
            return obter_resultados(eleicao_id, estrito=False)
        pausar()
        return None


def menu_resultados():
    eleicao = selecionar_eleicao("Selecione a eleição para ver resultados")
    if not eleicao:
        return

    limpar_tela()
    titulo(f"RESULTADOS — {eleicao['nome']}")
    print(f"  Status: {eleicao['status'].upper()} | Tipo: {eleicao['tipo'].capitalize()}")
    if eleicao.get("voto_ponderado"):
        print("  Modo: VOTO PONDERADO")
    print()

    resultados = _obter_resultados_ou_avisar(eleicao["id"])
    if resultados is None:
        return

    print("  ┌──────────────────────────────────────────────────────┐")
    print(f"  │  Eleitores aptos:            {resultados['total_eleitores']:>6}                 │")
    print(f"  │  Compareceram (≥1 cargo):    {resultados['votaram']:>6}                 │")
    print(f"  │  Votaram em todos os cargos: {resultados['concluiram_todos_cargos']:>6}                 │")
    print(f"  │  Abstenções:                 {resultados['abstencoes']:>6}                 │")
    print(f"  │  Votos computados:           {resultados['total_votos']:>6}                 │")
    if resultados["ponderado"]:
        print(f"  │  Peso total dos votos:       {resultados['total_peso']:>6.2f}                 │")
    print("  └──────────────────────────────────────────────────────┘\n")

    ponderado = resultados["ponderado"]
    for cargo, lista in resultados["por_cargo"].items():
        ag = resultados["agregado_cargo"].get(cargo, {})
        total_cargo = ag.get("total_peso" if ponderado else "total_votos", 0)
        print(f"  ── {cargo.upper()} ──  ({ag.get('total_votos', 0)} votos)\n")
        for i, c in enumerate(lista, 1):
            valor = c["peso_votos"] if ponderado else c["votos"]
            pct = (valor / total_cargo * 100) if total_cargo else 0
            barra = "█" * int(pct / 2) + "░" * (50 - int(pct / 2))
            med = "🥇" if i == 1 else f"{i}º"
            print(f"  {med}  Nº {c['numero']:02d} — {c['nome']}")
            if ponderado:
                print(f"      Votos: {c['votos']} | Peso: {c['peso_votos']:.2f} ({pct:.1f}%)")
            else:
                print(f"      Votos: {c['votos']} ({pct:.1f}%)")
            print(f"      [{barra}]\n")

        b, n = ag.get("brancos", {}), ag.get("nulos", {})
        linha = f"      BRANCO: {b.get('qtd', 0)}   NULO: {n.get('qtd', 0)}"
        if ponderado:
            linha += f"   (pesos {b.get('peso', 0):.2f} / {n.get('peso', 0):.2f})"
        print(linha + "\n")

    if resultados.get("falhas_decifra"):
        aviso(f"{len(resultados['falhas_decifra'])} voto(s) ilegíveis foram IGNORADOS.")

    if eleicao["status"] != "fechada":
        aviso("A eleição ainda não foi fechada. Resultados parciais.")

    pausar()


def menu_exportar():
    eleicao = selecionar_eleicao("Selecione a eleição para exportar")
    if not eleicao:
        return

    limpar_tela()
    titulo(f"EXPORTAR — {eleicao['nome']}")
    print("""
  [1] Resultados em CSV (planilha)
  [2] Resultados em PDF (boletim oficial)
  [3] Ambos (CSV + PDF)
  [4] Quadro público de compromissos (CSV, para afixar)
  [0] Cancelar
""")
    opcao = input("  Opção: ").strip()
    if opcao == "0":
        return
    if opcao not in ("1", "2", "3", "4"):
        erro("Opção inválida.")
        pausar()
        return

    try:
        if opcao in ("1", "3"):
            ok(f"CSV salvo em:\n     {exportar_resultados_csv(eleicao['id'])}")
        if opcao in ("2", "3"):
            ok(f"PDF salvo em:\n     {exportar_resultados_pdf(eleicao['id'])}")
        if opcao == "4":
            ok(f"Quadro salvo em:\n     {exportar_quadro_compromissos(eleicao['id'])}")
    except ErroApuracao as e:
        erro(str(e))
        print("  Corrija a integridade antes de emitir o boletim.")
    except Exception as e:
        erro(f"Erro na exportação: {e}")
    pausar()


def menu_listar_eleitores():
    eleicao = selecionar_eleicao("Selecione a eleição")
    if not eleicao:
        return

    limpar_tela()
    titulo(f"ELEITORES — {eleicao['nome']}")

    eleitores = listar_eleitores(eleicao["id"])
    if not eleitores:
        aviso("Nenhum eleitor cadastrado.")
        pausar()
        return

    total_cargos = len(listar_cargos(eleicao["id"]))
    tem_peso = eleicao.get("voto_ponderado")

    print(f"\n  {'ID':<5} {'Nome':<26} {'CPF':<15} {'Unid.':<7} "
          f"{'Peso':>5}  {'Cargos':<7} Situação")
    print("  " + "─" * 88)

    for e in eleitores:
        votados = e.get("cargos_votados", 0)
        if votados == 0:
            situacao = "— não votou"
        elif total_cargos and votados >= total_cargos:
            situacao = "✓ completo"
        else:
            situacao = "~ parcial"
        un = (e["unidade"] or "—")[:6]
        peso = f"{e['peso']:>5.2f}" if tem_peso else "    —"
        print(f"  {e['id']:<5} {e['nome'][:25]:<26} {e['cpf_formatado']:<15} {un:<7} "
              f"{peso}  {votados}/{total_cargos:<5} {situacao}")

    completos = sum(1 for e in eleitores
                    if total_cargos and e.get("cargos_votados", 0) >= total_cargos)
    parciais = sum(1 for e in eleitores
                   if 0 < e.get("cargos_votados", 0) < total_cargos)
    print(f"\n  Total: {len(eleitores)} | Completos: {completos} | "
          f"Parciais: {parciais} | Pendentes: {len(eleitores) - completos - parciais}")
    pausar()


def menu_relatorio_votos():
    """Relatório de presença: quem votou, sem revelar em quem."""
    eleicao = selecionar_eleicao("Selecione a eleição para o relatório de votos")
    if not eleicao:
        return

    limpar_tela()
    titulo(f"RELATÓRIO DE VOTOS — {eleicao['nome']}")
    print("  (Não revela em quem cada pessoa votou — sigilo preservado)\n")

    rel = relatorio_votos(eleicao["id"])
    print(f"  Cargos:          {', '.join(rel['cargos']) or '—'}")
    print(f"  Total eleitores: {rel['total_eleitores']}")
    print(f"  Compareceram:    {rel['qtd_votaram']}")
    print(f"  Votação parcial: {rel['qtd_parciais']}")
    print(f"  Pendentes:       {rel['qtd_pendentes']}")
    print(f"  Votos na urna:   {rel['total_votos_computados']}\n")

    print(f"  {'STATUS':<11} {'NOME':<26} {'CPF':<16} {'UNID.':<7} {'CARGOS':<7} {'DATA'}")
    print("  " + "─" * 88)
    for e in rel["votaram"]:
        un = (e.get("unidade") or "—")[:6]
        data = (e.get("data_voto") or "")[:19]
        marca = "✓ COMPLETO" if e["completo"] else "~ PARCIAL"
        cargos = f"{e['cargos_votados_qtd']}/{e['total_cargos']}"
        print(f"  {marca:<11} {e['nome'][:25]:<26} {e['cpf_formatado']:<16} {un:<7} {cargos:<7} {data}")
    for e in rel["pendentes"]:
        un = (e.get("unidade") or "—")[:6]
        print(f"  {'— PENDENTE':<11} {e['nome'][:25]:<26} {e['cpf_formatado']:<16} {un:<7} "
              f"{'0/' + str(e['total_cargos']):<7}")

    print("\n  [1] Exportar este relatório em CSV")
    print("  [0] Voltar")
    if input("\n  Opção: ").strip() == "1":
        try:
            ok(f"CSV salvo em:\n     {exportar_relatorio_votos_csv(eleicao['id'])}")
        except Exception as e:
            erro(str(e))
        pausar()


# ---------- auditoria ----------

def menu_auditoria():
    """Exploração do log de auditoria e integridade dos votos."""
    while True:
        limpar_tela()
        titulo("AUDITORIA DE VOTOS / LOG DO SISTEMA")
        print("""
  O log registra ações do sistema. Nos votos, registra que um voto
  ocorreu (cargo e peso), SEM identificar o eleitor nem o candidato
  escolhido — o sigilo é preservado.

  [1] Ver log recente (últimos 50)
  [2] Filtrar só votos registrados
  [3] Resumo por tipo de ação
  [4] Integridade de uma eleição (cruzamento)
  [5] Buscar no log (texto livre)
  [6] Exportar log em CSV
  [7] Quadro de compromissos + Merkle (ZK)
  [8] Verificar apuração criptográfica (ZK)
  [9] Estudar provas OR de Schnorr (demo)
  [V] Verificar provas OR dos votos (Python)
  [H] Apuração homomórfica (Paillier)
  [0] Voltar
""")
        op = input("  Opção: ").strip().upper()

        if op == "0":
            return

        if op == "H":
            eleicao = selecionar_eleicao("Selecione a eleição — apuração homomórfica")
            if not eleicao:
                continue
            limpar_tela()
            titulo(f"APURAÇÃO HOMOMÓRFICA — {eleicao['nome']}")
            try:
                from homo_voto import info_homo
                info = info_homo()
                print(f"\n  Esquema: {info['esquema']}")
                print(f"  Chave: {'presente' if info['chave_existe'] else 'será gerada'} "
                      f"({info.get('n_bits') or '?'} bits)")
                if info.get("aviso"):
                    aviso(info["aviso"])
                rel = obter_resultados_homo(eleicao["id"])
                print("\n  Totais (decifrados só no agregado):\n")
                for cargo, dados in rel["por_cargo"].items():
                    print(f"  ── {cargo} ──")
                    for c in dados["candidatos"]:
                        print(f"     Nº {c['numero']:02d} {c['nome']}: peso {c['peso_votos']:.2f}")
                    print(f"     Brancos: {dados['brancos_peso']:.2f} | "
                          f"Nulos: {dados['nulos_peso']:.2f}\n")
                print("  Os votos individuais permanecem cifrados; só a soma foi aberta.")
            except Exception as e:
                erro(str(e))
            pausar()
            continue

        if op == "9":
            limpar_tela()
            titulo("PROVAS OR DE SCHNORR — DEMO")
            print()
            try:
                import schnorr_or
                schnorr_or._demo()
            except Exception as e:
                erro(str(e))
            pausar()
            continue

        if op == "V":
            eleicao = selecionar_eleicao("Selecione a eleição — verificar provas OR")
            if not eleicao:
                continue
            limpar_tela()
            titulo(f"VERIFICAÇÃO OR DE SCHNORR — {eleicao['nome']}")
            rel = verificar_provas_or(eleicao["id"])
            print(f"""
  Total de votos:     {rel['total_votos']}
  Com prova OR:       {rel['com_prova_or']}
  Provas válidas:     {rel['provas_validas']}
  Provas inválidas:   {len(rel['provas_invalidas'])}
  Sem prova (branco/nulo/legado): {len(rel['sem_prova_or'])}
""")
            if rel["ok"]:
                ok("Todas as provas OR presentes são válidas.")
            else:
                erro("Há provas inválidas!")
                for inv in rel["provas_invalidas"][:10]:
                    print(f"    voto #{inv['voto_id']}: {inv['motivo']}")
            print("\n  A verificação não revela em quem cada eleitor votou.")
            pausar()
            continue

        if op == "1":
            _exibir_log(consultar_auditoria(limite=50))
            pausar()

        elif op == "2":
            regs = consultar_auditoria(acao="VOTO_REGISTRADO", limite=100)
            print(f"\n  Eventos de voto: {len(regs)}\n")
            _exibir_log(regs)
            pausar()

        elif op == "3":
            acoes = listar_acoes_auditoria()
            print(f"\n  {'AÇÃO':<25} {'QTD':>8}")
            print("  " + "─" * 35)
            for a in acoes:
                print(f"  {a['acao']:<25} {a['total']:>8}")
            pausar()

        elif op == "7":
            eleicao = selecionar_eleicao("Selecione a eleição — quadro de compromissos")
            if not eleicao:
                continue
            q = quadro_compromissos(eleicao["id"])
            limpar_tela()
            titulo(f"COMPROMISSOS ZK — {eleicao['nome']}")
            print(f"\n  Total de compromissos: {q['total']}")
            print(f"  Raiz Merkle: {q['merkle_raiz']}\n")
            for c in q["compromissos"][:20]:
                print(f"  voto #{c['voto_id']} [{(c['cargo'] or '—')[:12]:<12}] "
                      f"{c['compromisso'][:32]}…")
            if q["total"] > 20:
                print(f"  ... e mais {q['total'] - 20}")
            print("\n  Os compromissos são públicos; o conteúdo do voto permanece secreto.")
            print("  Use [6] Exportar no menu de exportação para afixar o quadro.")
            pausar()

        elif op == "8":
            eleicao = selecionar_eleicao("Selecione a eleição — verificação ZK")
            if not eleicao:
                continue
            limpar_tela()
            titulo(f"VERIFICAÇÃO CRIPTOGRÁFICA — {eleicao['nome']}")
            rel = verificar_votos_zk(eleicao["id"])
            if rel.get("ok"):
                ok("Todos os compromissos são válidos.")
            else:
                erro(f"Falhas: {len(rel.get('erros') or [])}")
                for e in (rel.get("erros") or [])[:10]:
                    print(f"    {e}")
                for f in (rel.get("falhas_decifra") or [])[:10]:
                    print(f"    voto #{f['voto_id']}: {f['erro']}")
            print(f"\n  Votos verificados: {rel.get('compromissos_validos')}/{rel.get('total_votos')}")
            print(f"  Peso total: {rel.get('peso_total')}")
            print(f"  Brancos: {rel.get('brancos')} | Nulos: {rel.get('nulos')}")
            print(f"  Raiz Merkle: {rel.get('merkle_raiz')}")
            if rel.get("contagem_por_candidato"):
                print("\n  Contagem (peso por candidato_id):")
                for cid, peso in rel["contagem_por_candidato"].items():
                    print(f"    candidato {cid}: {peso}")
            pausar()

        elif op == "4":
            eleicao = selecionar_eleicao("Selecione a eleição para auditoria de integridade")
            if not eleicao:
                continue
            limpar_tela()
            titulo(f"INTEGRIDADE — {eleicao['nome']}")
            dados = auditoria_consistencia_votos(eleicao["id"])

            print(f"""
  Registros de participação:        {dados['participacoes']}
  Votos na urna:                    {dados['votos_urna']}
  Eventos no log (VOTO_REGISTRADO): {dados['eventos_log']}
  Eleitores que votaram em tudo:    {dados['eleitores_completos']}
""")
            if dados["consistente"]:
                ok("Integridade OK — os três números batem.")
            else:
                erro("Divergência detectada!")
                print(f"  participação − urna = {dados['divergencia']['participacao_vs_urna']}")
                print(f"  urna − log          = {dados['divergencia']['urna_vs_log']}")

            if dados["por_cargo"]:
                print("\n  Votos por cargo na urna:")
                for cargo, info in dados["por_cargo"].items():
                    print(f"    {cargo}: {info['qtd']} (peso {info['peso']:.2f})")

            if dados["timeline"]:
                print(f"\n  Timeline de votos no log ({len(dados['timeline'])}):\n")
                for ev in dados["timeline"][:30]:
                    print(f"  [{ev['registrado_em']}] {ev['detalhes']}")
                if len(dados["timeline"]) > 30:
                    print(f"  ... e mais {len(dados['timeline']) - 30} eventos")
            pausar()

        elif op == "5":
            termo = input("\n  Texto para buscar: ").strip()
            if not termo:
                continue
            regs = consultar_auditoria(busca=termo, limite=100)
            print(f"\n  Encontrados: {len(regs)}\n")
            _exibir_log(regs)
            pausar()

        elif op == "6":
            regs = consultar_auditoria(limite=5000)
            if not regs:
                aviso("Nenhum registro para exportar.")
                pausar()
                continue
            pasta = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "relatorios")
            os.makedirs(pasta, exist_ok=True)
            caminho = os.path.join(
                pasta, f"auditoria_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            )
            try:
                exportar_auditoria_csv(caminho, regs)
                ok(f"Exportado {len(regs)} registros:\n     {caminho}")
            except Exception as e:
                erro(str(e))
            pausar()

        else:
            erro("Opção inválida!")
            pausar()


def _exibir_log(regs: list):
    """Exibe registros de auditoria formatados."""
    if not regs:
        aviso("Nenhum registro encontrado.")
        return
    print(f"  {'ID':<6} {'DATA/HORA':<22} {'AÇÃO':<20} {'DETALHES'}")
    print("  " + "─" * 90)
    for r in regs:
        data = (r.get("registrado_em") or "")[:19]
        acao = (r.get("acao") or "")[:18]
        det = (r.get("detalhes") or "")[:45]
        print(f"  {r['id']:<6} {data:<22} {acao:<20} {det}")


# ---------- banco ----------

def menu_banco_dados():
    """Backup, restauração e inspeção do banco SQLite."""
    while True:
        limpar_tela()
        titulo("BANCO DE DADOS SQLite")
        info = inspecionar_banco()
        print(f"\n  Arquivo:  {info['caminho']}")
        print(f"  Tamanho:  {info['tamanho_kb']} KB")
        print(f"  Existe:   {'Sim' if info['existe'] else 'Não'}\n")

        if info["tabelas"]:
            print(f"  {'TABELA':<20} {'REGISTROS':>10}")
            print("  " + "─" * 32)
            for t in info["tabelas"]:
                print(f"  {t['nome']:<20} {t['registros']:>10}")

        try:
            from crypto_voto import info_criptografia
            cripto = info_criptografia()
            print(f"\n  Criptografia: {cripto['algoritmo']}")
            print(f"  Arquivo da chave: {cripto['arquivo_chave']}")
            print(f"  Chave presente: {'Sim' if cripto['chave_existe'] else 'Não (criada no 1º voto)'}")
            if not cripto["chave_existe"]:
                print("  ⚠ Sem esse arquivo os votos já gravados NÃO podem ser apurados.")
        except Exception:
            pass

        try:
            from homo_voto import info_homo
            h = info_homo()
            if h.get("aviso"):
                print(f"\n  ⚠ Paillier: {h['aviso']}")
        except Exception:
            pass

        print("""
  [1] Fazer backup agora
  [2] Listar backups
  [3] Restaurar um backup
  [4] Ver colunas de cada tabela
  [0] Voltar
""")
        op = input("  Opção: ").strip()

        if op == "0":
            return
        if op == "1":
            try:
                ok(f"Backup criado:\n     {fazer_backup('manual')}")
            except Exception as e:
                erro(str(e))
            pausar()
        elif op == "2":
            _listar_backups()
            pausar()
        elif op == "3":
            _restaurar_backup_interativo()
        elif op == "4":
            print()
            for t in info["tabelas"]:
                print(f"  {t['nome']}: {', '.join(t['colunas'])}")
            pausar()
        else:
            erro("Opção inválida!")
            pausar()


def _listar_backups() -> list[dict]:
    backups = listar_backups()
    if not backups:
        aviso("Nenhum backup encontrado.")
        return []
    print(f"\n  {'#':<4} {'ARQUIVO':<45} {'KB':>8}  DATA")
    print("  " + "─" * 76)
    for i, b in enumerate(backups, 1):
        print(f"  {i:<4} {b['nome']:<45} {b['tamanho_kb']:>8}  {b['modificado']}")
    return backups


def _restaurar_backup_interativo():
    limpar_tela()
    titulo("RESTAURAR BACKUP")
    print("""
  A restauração SUBSTITUI o banco atual pelo backup escolhido.
  O estado atual é salvo automaticamente antes (antes_restaurar).

  ⚠ Votos registrados depois do backup serão PERDIDOS.
  ⚠ A chave urna.key NÃO faz parte do backup: se ela mudou desde
    então, os votos restaurados não poderão ser decifrados.
""")
    backups = _listar_backups()
    if not backups:
        pausar()
        return

    n = ler_int("\n  Número do backup a restaurar (0 = cancelar): ")
    if n is None or n == 0:
        pausar()
        return
    if not 1 <= n <= len(backups):
        erro("Número fora da lista.")
        pausar()
        return

    escolhido = backups[n - 1]
    print(f"\n  Selecionado: {escolhido['nome']} ({escolhido['modificado']})")
    if input('  Digite "RESTAURAR" para confirmar: ').strip() != "RESTAURAR":
        aviso("Restauração cancelada.")
        pausar()
        return

    try:
        destino = restaurar_backup(escolhido["caminho"])
        inicializar_banco()
        ok(f"Banco restaurado em:\n     {destino}")
    except Exception as e:
        erro(str(e))
    pausar()


# ---------- administradores ----------

def menu_administradores(admin: dict):
    while True:
        limpar_tela()
        titulo("ADMINISTRADORES DO SISTEMA")
        admins = listar_admins()
        print(f"\n  {'ID':<5} {'USUÁRIO':<18} {'NOME':<26} {'ATIVO':<7} SENHA INICIAL")
        print("  " + "─" * 76)
        for a in admins:
            print(f"  {a['id']:<5} {a['usuario']:<18} {a['nome'][:25]:<26} "
                  f"{'sim' if a['ativo'] else 'não':<7} {'sim' if a['trocar_senha'] else 'não'}")

        print("""
  [1] Criar administrador
  [2] Redefinir senha de um administrador
  [3] Desativar administrador
  [0] Voltar
""")
        op = input("  Opção: ").strip()

        if op == "0":
            return
        if op == "1":
            usuario = input("\n  Novo usuário: ").strip()
            nome = input("  Nome completo: ").strip()
            senha = getpass.getpass("  Senha inicial (mín. 8 caracteres): ")
            try:
                criar_admin(usuario, senha, nome, autor=admin["usuario"])
                ok(f"Administrador '{usuario}' criado. Ele deverá trocar a senha no 1º acesso.")
            except ValueError as e:
                erro(str(e))
            pausar()
        elif op == "2":
            usuario = input("\n  Usuário: ").strip()
            senha = getpass.getpass("  Nova senha (mín. 8 caracteres): ")
            try:
                if redefinir_senha_admin(usuario, senha, autor=admin["usuario"]):
                    ok("Senha redefinida. O usuário trocará no próximo acesso.")
                else:
                    erro("Usuário não encontrado ou inativo.")
            except ValueError as e:
                erro(str(e))
            pausar()
        elif op == "3":
            usuario = input("\n  Usuário a desativar: ").strip()
            if usuario == admin["usuario"]:
                erro("Você não pode desativar a si mesmo.")
                pausar()
                continue
            if not perguntar_sim(f"Confirma desativar '{usuario}'?"):
                continue
            try:
                if desativar_admin(usuario, autor=admin["usuario"]):
                    ok("Administrador desativado.")
                else:
                    erro("Usuário não encontrado ou já inativo.")
            except ValueError as e:
                erro(str(e))
            pausar()


def trocar_senha(admin: dict, senha_atual_conhecida: str | None = None) -> bool:
    if senha_atual_conhecida is None:
        limpar_tela()
        titulo("TROCAR SENHA")
        print(f"\n  Usuário: {admin['usuario']}\n")
        senha_atual = getpass.getpass("  Senha atual: ")
    else:
        senha_atual = senha_atual_conhecida

    senha_nova = getpass.getpass("  Nova senha (mín. 8 caracteres): ")
    senha_conf = getpass.getpass("  Confirme a nova senha: ")

    if senha_nova != senha_conf:
        erro("As senhas não coincidem.")
        pausar()
        return False

    try:
        sucesso = alterar_senha_admin(admin["usuario"], senha_atual, senha_nova)
    except ValueError as e:
        erro(str(e))
        pausar()
        return False

    if sucesso:
        admin["trocar_senha"] = False
        ok("Senha alterada com sucesso!")
    else:
        erro("Senha atual incorreta.")
    pausar()
    return sucesso


# ==================== ÁREA DE VOTAÇÃO ====================

def area_votacao():
    limpar_tela()
    titulo("ÁREA DE VOTAÇÃO")

    eleicoes_abertas = listar_eleicoes(status="aberta")
    if not eleicoes_abertas:
        aviso("Não há nenhuma eleição aberta para votação no momento.")
        print("  Peça ao administrador para abrir a votação.")
        pausar()
        return

    print("\n  Eleições disponíveis:\n")
    for e in eleicoes_abertas:
        pond = " [Ponderado]" if e.get("voto_ponderado") else ""
        cargos = listar_cargos(e["id"])
        print(f"  [{e['id']}] {e['nome']} ({e['tipo'].capitalize()}){pond}")
        print(f"       Cargos: {', '.join(cargos)}")

    eid = ler_int("\n  Digite o ID da eleição (0 para cancelar): ")
    if eid is None:
        pausar()
        return
    if eid == 0:
        return

    eleicao = obter_eleicao(eid)
    if not eleicao or eleicao["status"] != "aberta":
        erro("Eleição inválida ou não está aberta.")
        pausar()
        return

    limpar_tela()
    titulo(f"VOTAÇÃO — {eleicao['nome']}")
    print("\n  Identifique-se com seu CPF.")
    print("  (O sistema controla quem já votou, cargo a cargo)\n")

    cpf = input("  CPF: ").strip()
    eleitor = autenticar_eleitor(eleicao["id"], cpf)

    if not eleitor:
        erro("CPF não encontrado nesta eleição ou inválido.")
        pausar()
        return

    pendentes = eleitor["cargos_pendentes"]
    if not pendentes:
        print(f"\n  Atenção, {eleitor['nome']}!")
        print(f"  O CPF {formatar_cpf(eleitor['documento'])} já votou em todos")
        print(f"  os cargos desta eleição: {', '.join(eleitor['cargos_votados'])}.")
        pausar()
        return

    if eleitor["cargos_votados"]:
        print(f"\n  Você já votou em: {', '.join(eleitor['cargos_votados'])}")
        print(f"  Faltam: {', '.join(pendentes)}")
        pausar()

    realizar_votacao(eleicao, eleitor, pendentes)


def realizar_votacao(eleicao: dict, eleitor: dict, pendentes: list[str]):
    """Conduz o eleitor por um voto em cada cargo pendente."""
    recibos: list[tuple[str, str]] = []

    for indice, cargo in enumerate(pendentes, 1):
        resultado = votar_um_cargo(eleicao, eleitor, cargo, indice, len(pendentes))
        if resultado is None:  # cancelou
            break
        recibos.append(resultado)

    limpar_tela()
    if not recibos:
        print("\n  Votação encerrada sem registrar votos.")
        pausar()
        return

    print("""
  ╔══════════════════════════════════════════════════╗
  ║              VOTO(S) REGISTRADO(S)               ║
  ║        (cifrado + compromisso criptográfico)     ║
  ╚══════════════════════════════════════════════════╝
""")
    print("  Seus recibos — guarde para conferência:\n")
    for cargo, comp in recibos:
        print(f"  {cargo}:")
        print(f"    {comp}\n")

    faltaram = [c for c in pendentes if c not in [r[0] for r in recibos]]
    if faltaram:
        aviso(f"Você ainda NÃO votou para: {', '.join(faltaram)}")
        print("  Pode retornar à Área de Votação para concluir.")
    else:
        print("  Você votou em todos os cargos. Obrigado!")

    print("\n  Com esses códigos você confere, no menu principal [4],")
    print("  se seus votos entraram no quadro público — sem revelar a escolha.")
    pausar()


def votar_um_cargo(eleicao: dict, eleitor: dict, cargo: str,
                   indice: int, total: int) -> tuple[str, str] | None:
    """Mostra a cédula de um cargo e registra o voto. None = cancelado."""
    candidatos = listar_candidatos(eleicao["id"], cargo=cargo)

    while True:
        limpar_tela()
        titulo(f"URNA ELETRÔNICA — {eleicao['nome']}")
        print(f"\n  Eleitor: {eleitor['nome']}")
        if eleitor.get("unidade"):
            print(f"  Unidade: {eleitor['unidade']} {eleitor.get('bloco') or ''}")
        if eleicao.get("voto_ponderado"):
            print(f"  Peso do seu voto: {eleitor.get('peso', 1.0)}")
        print(f"\n  ══ CÉDULA {indice} de {total} — {cargo.upper()} ══\n")

        for c in candidatos:
            print(f"  Nº {c['numero']:02d}  —  {c['nome']}")
            if c.get("descricao"):
                print(f"           {c['descricao']}")
            print()

        print("  ─────────────────────────────────────")
        print("  Digite o NÚMERO do candidato")
        print("  Ou digite BRANCO ou NULO")
        print("  Ou digite 0 para cancelar a votação\n")

        escolha = input("  Seu voto: ").strip().upper()

        if escolha == "0":
            print("\n  Votação cancelada.")
            pausar()
            return None

        candidato = None
        if escolha == "BRANCO":
            tipo = "branco"
        elif escolha == "NULO":
            tipo = "nulo"
        else:
            try:
                numero = int(escolha)
            except ValueError:
                erro("Entrada inválida. Digite o número, BRANCO, NULO ou 0.")
                pausar()
                continue
            candidato = obter_candidato_por_numero(eleicao["id"], numero, cargo=cargo)
            if not candidato:
                erro(f"O número {numero} não é candidato a {cargo}.")
                pausar()
                continue
            tipo = "candidato"

        if not confirmar_voto(eleicao, eleitor, cargo, tipo, candidato):
            continue  # volta para a mesma cédula

        try:
            voto_id = registrar_voto(
                eleicao["id"], eleitor["id"], cargo, tipo,
                candidato["id"] if candidato else None,
            )
        except ValueError as e:
            erro(str(e))
            pausar()
            return None
        except Exception as e:
            erro(f"Erro ao registrar voto: {e}")
            pausar()
            return None

        recibo = obter_recibo_voto(voto_id) or {}
        return (cargo, recibo.get("compromisso", "—"))


def confirmar_voto(eleicao: dict, eleitor: dict, cargo: str,
                   tipo: str, candidato: dict | None) -> bool:
    """Tela de confirmação. True = confirmado, False = corrigir."""
    limpar_tela()
    titulo(f"CONFIRMAÇÃO DO VOTO — {cargo.upper()}")

    print("\n  ╔══════════════════════════════════════╗")
    if tipo == "candidato" and candidato:
        print(f"  ║  Nº {candidato['numero']:02d}                              ║")
        print(f"  ║  {candidato['nome'][:34]:<34} ║")
        print(f"  ║  {candidato['cargo'][:34]:<34} ║")
    elif tipo == "branco":
        print("  ║                                      ║")
        print("  ║         VOTO EM BRANCO               ║")
        print("  ║                                      ║")
    else:
        print("  ║                                      ║")
        print("  ║           VOTO NULO                  ║")
        print("  ║                                      ║")
    print("  ╚══════════════════════════════════════╝")

    if eleicao.get("voto_ponderado"):
        print(f"\n  Peso deste voto: {eleitor.get('peso', 1.0)}")

    print("\n  Confirma este voto?")
    print("  [1] SIM, confirmar")
    print("  [2] NÃO, corrigir")
    return input("\n  Opção: ").strip() == "1"


# ==================== INICIALIZAÇÃO ====================

if __name__ == "__main__":
    try:
        inicializar_banco()
        menu_principal()
    except KeyboardInterrupt:
        print("\n\n  Sistema encerrado pelo usuário.\n")
        sys.exit(0)
    except Exception as e:
        print(f"\n  Erro crítico: {e}")
        sys.exit(1)
