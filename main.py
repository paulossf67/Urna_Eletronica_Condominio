#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║     SISTEMA DE URNA ELETRÔNICA                               ║
║     Para Condomínios • Associações • Sindicatos              ║
╚══════════════════════════════════════════════════════════════╝

Versão: 2.0 — Mais fácil de usar
"""

import os
import sys
from database import (
    inicializar_banco, verificar_admin, alterar_senha_admin,
    fazer_backup, listar_backups, inspecionar_banco, DB_PATH,
    consultar_auditoria, listar_acoes_auditoria, auditoria_consistencia_votos,
    exportar_auditoria_csv,
)
from eleicao import (
    criar_eleicao, listar_eleicoes, obter_eleicao, alterar_status_eleicao,
    cadastrar_candidato, listar_candidatos, obter_candidato_por_numero,
    cadastrar_eleitor, listar_eleitores, autenticar_eleitor, marcar_como_votou,
    registrar_voto, obter_resultados, criar_eleicao_demonstracao,
    exportar_resultados_csv, exportar_resultados_pdf, listar_cargos,
    relatorio_votos, exportar_relatorio_votos_csv, formatar_cpf,
    obter_recibo_voto, quadro_compromissos, verificar_votos_zk,
    verificar_provas_or, obter_resultados_homo,
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


# ==================== MENU PRINCIPAL ====================

def menu_principal():
    while True:
        limpar_tela()
        print("""
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║              URNA ELETRÔNICA  v2.0                           ║
║     Condomínios • Associações • Sindicatos                   ║
║                                                              ║
║         Sistema fácil, seguro e transparente                 ║
╚══════════════════════════════════════════════════════════════╝

  [1] Área do Administrador
  [2] Área de Votação (Eleitor)
  [3] Criar Eleição de Demonstração (para testar)
  [0] Sair
""")
        opcao = input("  Escolha uma opção: ").strip()

        if opcao == "1":
            login_admin()
        elif opcao == "2":
            area_votacao()
        elif opcao == "3":
            criar_demo()
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
  • 4 candidatos (Síndico e Conselheiro)
  • 6 eleitores (identificação pelo CPF)

  CPFs válidos (algoritmo oficial) dos eleitores de demonstração:
    123.456.789-09  |  234.567.890-92  |  345.678.901-75
    456.789.012-49  |  567.890.123-03  |  678.901.234-69

  Na votação, o eleitor se identifica digitando o CPF.
""")
    conf = input("  Confirma criação? (s/N): ").strip().lower()
    if conf != "s":
        return
    try:
        eid = criar_eleicao_demonstracao()
        ok(f"Eleição de demonstração criada! ID: {eid}")
        print("\n  Agora faça login como administrador, abra a votação")
        print("  e teste na Área de Votação digitando um dos CPFs acima.")
    except Exception as e:
        erro(f"Erro: {e}")
    pausar()


# ==================== ADMINISTRAÇÃO ====================

def login_admin():
    limpar_tela()
    titulo("LOGIN DO ADMINISTRADOR")
    print("\n  Padrão:  usuario = admin   /   senha = admin123\n")

    usuario = input("  Usuário: ").strip()
    senha = input("  Senha: ").strip()

    admin = verificar_admin(usuario, senha)
    if admin:
        menu_admin(admin)
    else:
        erro("Usuário ou senha incorretos!")
        pausar()


def menu_admin(admin: dict):
    while True:
        limpar_tela()
        titulo(f"PAINEL ADMINISTRATIVO — {admin['nome']}")
        print("""
  [1] Gerenciar Eleições
  [2] Cadastrar Candidatos
  [3] Cadastrar Eleitores
  [4] Abrir / Fechar Votação
  [5] Ver Resultados
  [6] Exportar Resultados (CSV / PDF)
  [7] Listar Eleitores (quem já votou)
  [8] Relatório de Votos (presença)
  [9] Banco de Dados (backup / inspeção)
  [B] Auditoria de Votos / Log
  [A] Trocar minha senha
  [0] Voltar ao Menu Principal
""")
        opcao = input("  Escolha uma opção: ").strip().upper()

        if opcao == "1":
            gerenciar_eleicoes()
        elif opcao == "2":
            menu_cadastrar_candidatos()
        elif opcao == "3":
            menu_cadastrar_eleitores()
        elif opcao == "4":
            menu_status_votacao()
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

    try:
        eid = int(input("\n  Digite o ID da eleição (0 para cancelar): ").strip())
        if eid == 0:
            return None
        eleicao = obter_eleicao(eid)
        if not eleicao:
            erro("Eleição não encontrada.")
            pausar()
            return None
        return eleicao
    except ValueError:
        erro("ID inválido.")
        pausar()
        return None


def gerenciar_eleicoes():
    while True:
        limpar_tela()
        titulo("GERENCIAR ELEIÇÕES")

        eleicoes = listar_eleicoes()
        if eleicoes:
            print("\n  Eleições cadastradas:\n")
            for e in eleicoes:
                icon = {"preparacao": "🔧", "aberta": "🟢", "fechada": "🔴"}.get(e["status"], "?")
                pond = " | Voto ponderado" if e.get("voto_ponderado") else ""
                print(f"  [{e['id']}] {icon} {e['nome']}")
                print(f"       Tipo: {e['tipo'].capitalize()} | Status: {e['status'].upper()}{pond}")
                if e["descricao"]:
                    print(f"       {e['descricao']}")
                print()
        else:
            print("\n  Nenhuma eleição cadastrada ainda.\n")

        print("  [1] Criar nova eleição")
        print("  [0] Voltar")
        opcao = input("\n  Opção: ").strip()

        if opcao == "1":
            criar_nova_eleicao()
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
    tipo_op = input("  Escolha: ").strip()
    tipos = {"1": "condominio", "2": "associacao", "3": "sindicato"}
    tipo = tipos.get(tipo_op)
    if not tipo:
        erro("Tipo inválido!")
        pausar()
        return

    print("\n  Usar voto ponderado? (ex: fração ideal no condomínio)")
    print("  [1] Sim — cada eleitor tem um peso")
    print("  [2] Não — um eleitor = um voto")
    pond_op = input("  Escolha [2]: ").strip() or "2"
    voto_ponderado = pond_op == "1"

    try:
        eid = criar_eleicao(nome, descricao, tipo, voto_ponderado)
        ok(f"Eleição criada com sucesso! ID: {eid}")
        if voto_ponderado:
            print("  Lembre-se de informar o peso de cada eleitor no cadastro.")
    except Exception as e:
        erro(f"Erro: {e}")
    pausar()


def menu_cadastrar_candidatos():
    eleicao = selecionar_eleicao("Selecione a eleição para cadastrar candidatos")
    if not eleicao:
        return

    if eleicao["status"] != "preparacao":
        erro("Só é possível cadastrar candidatos em eleições em preparação.")
        pausar()
        return

    while True:
        limpar_tela()
        titulo(f"CANDIDATOS — {eleicao['nome']}")

        candidatos = listar_candidatos(eleicao["id"])
        if candidatos:
            print("\n  Candidatos cadastrados:\n")
            cargo_atual = None
            for c in candidatos:
                if c["cargo"] != cargo_atual:
                    cargo_atual = c["cargo"]
                    print(f"  ── {cargo_atual} ──")
                print(f"     Nº {c['numero']:02d} — {c['nome']}")
                if c.get("descricao"):
                    print(f"            {c['descricao']}")
        else:
            print("\n  Nenhum candidato cadastrado ainda.\n")

        print("\n  [1] Cadastrar novo candidato")
        print("  [0] Voltar")
        opcao = input("\n  Opção: ").strip()

        if opcao == "1":
            try:
                numero = int(input("\n  Número do candidato: ").strip())
                nome = input("  Nome completo: ").strip()
                cargo = input("  Cargo pretendido (ex: Síndico, Conselheiro): ").strip()
                descricao = input("  Descrição/proposta (opcional): ").strip()

                if not nome or not cargo:
                    erro("Nome e cargo são obrigatórios!")
                    pausar()
                    continue

                cid = cadastrar_candidato(eleicao["id"], numero, nome, cargo, descricao)
                ok(f"Candidato cadastrado! ID: {cid}")
            except ValueError as e:
                erro(str(e))
            except Exception as e:
                erro(f"Erro inesperado: {e}")
            pausar()
        elif opcao == "0":
            return


def menu_cadastrar_eleitores():
    eleicao = selecionar_eleicao("Selecione a eleição para cadastrar eleitores")
    if not eleicao:
        return

    if eleicao["status"] == "fechada":
        erro("Não é possível cadastrar eleitores em eleição fechada.")
        pausar()
        return

    while True:
        limpar_tela()
        titulo(f"ELEITORES — {eleicao['nome']}")

        eleitores = listar_eleitores(eleicao["id"])
        print(f"\n  Total de eleitores: {len(eleitores)}")
        if eleicao.get("voto_ponderado"):
            print("  (Esta eleição usa voto ponderado — informe o peso de cada um)\n")
        else:
            print()

        print("  [1] Cadastrar um eleitor")
        print("  [2] Cadastrar vários de uma vez (lote)")
        print("  [0] Voltar")
        opcao = input("\n  Opção: ").strip()

        if opcao == "1":
            cadastrar_um_eleitor(eleicao)
        elif opcao == "2":
            cadastrar_lote_eleitores(eleicao)
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

    if not nome or not cpf:
        erro("Nome e CPF são obrigatórios!")
        pausar()
        return

    try:
        eid = cadastrar_eleitor(eleicao["id"], nome, cpf, unidade=unidade, bloco=bloco, peso=peso)
        ok(f"Eleitor cadastrado! ID: {eid}")
        print(f"  CPF: {cpf}")
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
            print(f"    ✓ {nome} (CPF {cpf})")
        except ValueError as e:
            print(f"    ✗ {nome}: {e}")

    print(f"\n  Total cadastrados neste lote: {contador}")
    pausar()


def menu_status_votacao():
    eleicao = selecionar_eleicao("Selecione a eleição para alterar status")
    if not eleicao:
        return

    limpar_tela()
    titulo(f"STATUS DA ELEIÇÃO — {eleicao['nome']}")
    print(f"\n  Status atual: {eleicao['status'].upper()}\n")

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
        if not candidatos:
            erro("Não é possível abrir sem candidatos cadastrados!")
            pausar()
            return
        if not eleitores:
            erro("Não é possível abrir sem eleitores cadastrados!")
            pausar()
            return
        print(f"\n  Candidatos: {len(candidatos)} | Eleitores: {len(eleitores)}")
        conf = input("  Confirma abertura da votação? (s/N): ").strip().lower()
        if conf != "s":
            return

    if novo == "fechada":
        conf = input("\n  Confirma FECHAMENTO da votação? (s/N): ").strip().lower()
        if conf != "s":
            return

    try:
        # Backup automático antes de abrir ou fechar
        if novo in ("aberta", "fechada"):
            try:
                bk = fazer_backup(f"antes_{novo}")
                print(f"\n  Backup automático: {os.path.basename(bk)}")
            except Exception:
                pass
        alterar_status_eleicao(eleicao["id"], novo)
        ok(f"Status alterado para: {novo.upper()}")
    except Exception as e:
        erro(str(e))
    pausar()


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

    resultados = obter_resultados(eleicao["id"])

    print("  ┌──────────────────────────────────────────────────────┐")
    print(f"  │  Eleitores aptos:            {resultados['total_eleitores']:>6}                 │")
    print(f"  │  Compareceram:               {resultados['votaram']:>6}                 │")
    print(f"  │  Abstenções:                 {resultados['abstencoes']:>6}                 │")
    print(f"  │  Votos computados:           {resultados['total_votos']:>6}                 │")
    if resultados["ponderado"]:
        print(f"  │  Peso total dos votos:       {resultados['total_peso']:>6.2f}                 │")
    print("  └──────────────────────────────────────────────────────┘\n")

    for cargo, lista in resultados["por_cargo"].items():
        print(f"  ── {cargo.upper()} ──\n")
        total_cargo = sum(c["peso_votos"] for c in lista) if resultados["ponderado"] else sum(c["votos"] for c in lista)
        for i, c in enumerate(lista, 1):
            valor = c["peso_votos"] if resultados["ponderado"] else c["votos"]
            pct = (valor / total_cargo * 100) if total_cargo > 0 else 0
            barra = "█" * int(pct / 2) + "░" * (50 - int(pct / 2))
            med = "🥇" if i == 1 else f"{i}º"
            print(f"  {med}  Nº {c['numero']:02d} — {c['nome']}")
            if resultados["ponderado"]:
                print(f"      Votos: {c['votos']} | Peso: {c['peso_votos']:.2f} ({pct:.1f}%)")
            else:
                print(f"      Votos: {c['votos']} ({pct:.1f}%)")
            print(f"      [{barra}]\n")

    print(f"  Votos em BRANCO: {resultados['brancos']['qtd']}", end="")
    if resultados["ponderado"]:
        print(f" (peso {resultados['brancos']['peso']:.2f})")
    else:
        print()
    print(f"  Votos NULOS:     {resultados['nulos']['qtd']}", end="")
    if resultados["ponderado"]:
        print(f" (peso {resultados['nulos']['peso']:.2f})")
    else:
        print()

    if eleicao["status"] != "fechada":
        aviso("A eleição ainda não foi fechada. Resultados parciais.")

    pausar()


def menu_exportar():
    eleicao = selecionar_eleicao("Selecione a eleição para exportar resultados")
    if not eleicao:
        return

    limpar_tela()
    titulo(f"EXPORTAR RESULTADOS — {eleicao['nome']}")
    print("""
  [1] Exportar para CSV (planilha)
  [2] Exportar para PDF (boletim oficial)
  [3] Exportar ambos
  [0] Cancelar
""")
    opcao = input("  Opção: ").strip()
    if opcao == "0":
        return

    try:
        if opcao in ("1", "3"):
            caminho = exportar_resultados_csv(eleicao["id"])
            ok(f"CSV salvo em:\n     {caminho}")
        if opcao in ("2", "3"):
            caminho = exportar_resultados_pdf(eleicao["id"])
            ok(f"PDF salvo em:\n     {caminho}")
        if opcao not in ("1", "2", "3"):
            erro("Opção inválida.")
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

    tem_peso = eleicao.get("voto_ponderado")
    if tem_peso:
        print(f"\n  {'ID':<5} {'Nome':<26} {'CPF':<15} {'Unid.':<8} {'Peso':>5}  Votou?")
        print("  " + "─" * 78)
    else:
        print(f"\n  {'ID':<5} {'Nome':<28} {'CPF':<16} {'Unidade':<10} Votou?")
        print("  " + "─" * 75)

    for e in eleitores:
        status = "✓ SIM" if e["ja_votou"] else "— NÃO"
        un = (e["unidade"] or "—")[:7]
        cpf_fmt = formatar_cpf(e["documento"])
        if tem_peso:
            print(f"  {e['id']:<5} {e['nome'][:25]:<26} {cpf_fmt:<15} {un:<8} {e['peso']:>5.2f}  {status}")
        else:
            print(f"  {e['id']:<5} {e['nome'][:27]:<28} {cpf_fmt:<16} {un:<10} {status}")

    votaram = sum(1 for e in eleitores if e["ja_votou"])
    print(f"\n  Total: {len(eleitores)} | Votaram: {votaram} | Pendentes: {len(eleitores) - votaram}")
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
    print(f"  Total eleitores: {rel['total_eleitores']}")
    print(f"  Já votaram:      {rel['qtd_votaram']}")
    print(f"  Pendentes:       {rel['qtd_pendentes']}")
    print(f"  Votos na urna:   {rel['total_votos_computados']}\n")

    print(f"  {'STATUS':<10} {'NOME':<28} {'CPF':<16} {'UNID.':<8} {'DATA'}")
    print("  " + "─" * 80)
    for e in rel["votaram"]:
        un = (e.get("unidade") or "—")[:7]
        data = (e.get("data_voto") or "")[:19]
        print(f"  {'✓ VOTOU':<10} {e['nome'][:27]:<28} {e['cpf_formatado']:<16} {un:<8} {data}")
    for e in rel["pendentes"]:
        un = (e.get("unidade") or "—")[:7]
        print(f"  {'— PEND.':<10} {e['nome'][:27]:<28} {e['cpf_formatado']:<16} {un:<8}")

    print("\n  [1] Exportar este relatório em CSV")
    print("  [0] Voltar")
    op = input("\n  Opção: ").strip()
    if op == "1":
        try:
            caminho = exportar_relatorio_votos_csv(eleicao["id"])
            ok(f"CSV salvo em:\n     {caminho}")
        except Exception as e:
            erro(str(e))
        pausar()


def menu_auditoria():
    """Exploração do log de auditoria e integridade dos votos."""
    while True:
        limpar_tela()
        titulo("AUDITORIA DE VOTOS / LOG DO SISTEMA")
        print("""
  O log registra ações do sistema. Nos votos, registra que um voto
  ocorreu (tipo e peso), SEM identificar o eleitor nem o candidato
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
                print(f"  Chave: {'presente' if info['chave_existe'] else 'será gerada'} ({info.get('n_bits') or '?'} bits)")
                rel = obter_resultados_homo(eleicao["id"])
                print("\n  Totais (decifrados só no agregado):\n")
                for c in rel["candidatos"]:
                    print(f"  Nº {c['numero']:02d} {c['nome']}: peso {c['peso_votos']:.2f}")
                print(f"\n  Brancos (peso): {rel['brancos_peso']:.2f}")
                print(f"  Nulos (peso):   {rel['nulos_peso']:.2f}")
                print("\n  Os votos individuais permanecem cifrados; só a soma foi aberta.")
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
            regs = consultar_auditoria(limite=50)
            _exibir_log(regs)
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
                print(f"  voto #{c['voto_id']}: {c['compromisso'][:32]}…")
            if q["total"] > 20:
                print(f"  ... e mais {q['total'] - 20}")
            print("\n  Os compromissos são públicos; o conteúdo do voto permanece secreto.")
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
  Eleitores que já votaram:     {dados['eleitores_votaram']}
  Votos na urna:                {dados['votos_urna']}
  Eventos no log (VOTO_REGISTRADO): {dados['eventos_log']}
""")
            if dados["consistente"]:
                ok("Integridade OK — os três números batem.")
            else:
                erro("Divergência detectada!")
                print(f"  eleitores − urna = {dados['divergencia']['eleitores_vs_urna']}")
                print(f"  urna − log       = {dados['divergencia']['urna_vs_log']}")

            if dados["por_tipo"]:
                print("\n  Votos por tipo na urna:")
                for tipo, info in dados["por_tipo"].items():
                    print(f"    {tipo}: {info['qtd']} (peso {info['peso']:.2f})")

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
            caminho = os.path.join(
                os.path.dirname(os.path.abspath(DB_PATH)),
                f"auditoria_{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
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


def menu_banco_dados():
    """Backup e inspeção do banco SQLite."""
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
        except Exception:
            pass

        print("""
  [1] Fazer backup agora
  [2] Listar backups
  [3] Ver colunas de cada tabela
  [0] Voltar
""")
        op = input("  Opção: ").strip()

        if op == "0":
            return
        if op == "1":
            try:
                caminho = fazer_backup("manual")
                ok(f"Backup criado:\n     {caminho}")
            except Exception as e:
                erro(str(e))
            pausar()
        elif op == "2":
            backups = listar_backups()
            if not backups:
                aviso("Nenhum backup encontrado.")
            else:
                print(f"\n  {'ARQUIVO':<45} {'KB':>8}  DATA")
                print("  " + "─" * 70)
                for b in backups:
                    print(f"  {b['nome']:<45} {b['tamanho_kb']:>8}  {b['modificado']}")
            pausar()
        elif op == "3":
            print()
            for t in info["tabelas"]:
                print(f"  {t['nome']}: {', '.join(t['colunas'])}")
            pausar()
        else:
            erro("Opção inválida!")
            pausar()


def trocar_senha(admin: dict):
    limpar_tela()
    titulo("TROCAR SENHA")
    print(f"\n  Usuário: {admin['usuario']}\n")

    senha_atual = input("  Senha atual: ").strip()
    senha_nova = input("  Nova senha: ").strip()
    senha_conf = input("  Confirme a nova senha: ").strip()

    if not senha_nova:
        erro("A nova senha não pode ser vazia.")
        pausar()
        return
    if senha_nova != senha_conf:
        erro("As senhas não coincidem.")
        pausar()
        return

    if alterar_senha_admin(admin["usuario"], senha_atual, senha_nova):
        ok("Senha alterada com sucesso!")
    else:
        erro("Senha atual incorreta.")
    pausar()


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
        print(f"  [{e['id']}] {e['nome']} ({e['tipo'].capitalize()}){pond}")

    try:
        eid = int(input("\n  Digite o ID da eleição (0 para cancelar): ").strip())
        if eid == 0:
            return
        eleicao = obter_eleicao(eid)
        if not eleicao or eleicao["status"] != "aberta":
            erro("Eleição inválida ou não está aberta.")
            pausar()
            return
    except ValueError:
        erro("ID inválido.")
        pausar()
        return

    limpar_tela()
    titulo(f"VOTAÇÃO — {eleicao['nome']}")
    print("\n  Identifique-se com seu CPF.")
    print("  (O sistema controla quem já votou pelo CPF)\n")

    cpf = input("  CPF: ").strip()
    eleitor = autenticar_eleitor(eleicao["id"], cpf)

    if not eleitor:
        erro("CPF não encontrado nesta eleição ou inválido.")
        pausar()
        return

    if eleitor["ja_votou"]:
        print(f"\n  Atenção, {eleitor['nome']}!")
        print(f"  O CPF {formatar_cpf(eleitor['documento'])} já registrou voto nesta eleição.")
        print("  Cada CPF pode votar apenas uma vez.")
        pausar()
        return

    realizar_voto(eleicao, eleitor)


def realizar_voto(eleicao: dict, eleitor: dict):
    candidatos = listar_candidatos(eleicao["id"])
    cargos = listar_cargos(eleicao["id"])

    while True:
        limpar_tela()
        titulo(f"URNA ELETRÔNICA — {eleicao['nome']}")
        print(f"\n  Eleitor: {eleitor['nome']}")
        if eleitor.get("unidade"):
            print(f"  Unidade: {eleitor['unidade']} {eleitor.get('bloco') or ''}")
        if eleicao.get("voto_ponderado"):
            print(f"  Peso do seu voto: {eleitor.get('peso', 1.0)}")
        print()

        # Mostra candidatos agrupados por cargo
        cargo_atual = None
        for c in candidatos:
            if c["cargo"] != cargo_atual:
                cargo_atual = c["cargo"]
                print(f"  ── {cargo_atual} ──")
            print(f"  Nº {c['numero']:02d}  —  {c['nome']}")
            if c.get("descricao"):
                print(f"           {c['descricao']}")
            print()

        print("  ─────────────────────────────────────")
        print("  Digite o NÚMERO do candidato")
        print("  Ou digite BRANCO ou NULO")
        print("  Ou digite 0 para cancelar\n")

        escolha = input("  Seu voto: ").strip().upper()

        if escolha == "0":
            print("\n  Votação cancelada.")
            pausar()
            return

        if escolha == "BRANCO":
            confirmar_voto(eleicao, eleitor, "branco")
            return

        if escolha == "NULO":
            confirmar_voto(eleicao, eleitor, "nulo")
            return

        try:
            numero = int(escolha)
            candidato = obter_candidato_por_numero(eleicao["id"], numero)
            if not candidato:
                erro(f"Número {numero} não corresponde a nenhum candidato.")
                pausar()
                continue
            confirmar_voto(eleicao, eleitor, "candidato", candidato)
            return
        except ValueError:
            erro("Entrada inválida. Digite o número, BRANCO, NULO ou 0.")
            pausar()


def confirmar_voto(eleicao: dict, eleitor: dict, tipo: str, candidato: dict | None = None):
    limpar_tela()
    titulo("CONFIRMAÇÃO DO VOTO")

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
    opcao = input("\n  Opção: ").strip()

    if opcao != "1":
        realizar_voto(eleicao, eleitor)
        return

    try:
        peso = float(eleitor.get("peso") or 1.0)
        if tipo == "candidato":
            voto_id = registrar_voto(eleicao["id"], "candidato", candidato["id"], peso)
        else:
            voto_id = registrar_voto(eleicao["id"], tipo, peso=peso)

        marcar_como_votou(eleitor["id"])
        recibo = obter_recibo_voto(voto_id)

        limpar_tela()
        print("""
  ╔══════════════════════════════════════════════════╗
  ║              VOTO REGISTRADO                     ║
  ║         (cifrado + compromisso criptográfico)    ║
  ╚══════════════════════════════════════════════════╝
""")
        if recibo and recibo.get("compromisso"):
            print("  Seu recibo (compromisso) — guarde para conferência:")
            print(f"\n  {recibo['compromisso']}\n")
            print("  Com este código você pode verificar se o voto")
            print("  entrou no quadro público, sem revelar sua escolha.")
        print("\n  Obrigado por votar!")
        pausar()
    except Exception as e:
        erro(f"Erro ao registrar voto: {e}")
        pausar()


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
