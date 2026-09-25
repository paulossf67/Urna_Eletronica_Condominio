#!/usr/bin/env python3
"""
Terminal de RELATÓRIOS em rede.

As rotas de resultados, presença e auditoria exigem login administrativo —
este terminal pede usuário/senha e guarda o token só em memória.

Uso:
  python3 cliente_relatorio.py --servidor 192.168.1.10
  python3 cliente_relatorio.py --servidor 192.168.1.10 --porta 8080
"""

import argparse
import getpass
import json
import os
import sys
import urllib.request
import urllib.error


def limpar():
    os.system("cls" if os.name == "nt" else "clear")


def pausar():
    input("\n  Pressione ENTER para continuar...")


class Cliente:
    """Cliente HTTP da urna com sessão administrativa."""

    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.token: str | None = None
        self.admin: dict | None = None

    def pedir(self, metodo: str, path: str, dados: dict | None = None) -> dict:
        url = self.base + path
        body = None
        headers = {"Accept": "application/json"}
        if dados is not None:
            body = json.dumps(dados).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        req = urllib.request.Request(url, data=body, headers=headers, method=metodo)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode("utf-8"))
            except Exception:
                return {"ok": False, "erro": f"HTTP {e.code}"}
            if e.code == 401:
                self.token = None
                self.admin = None
                payload["_expirou"] = True
            return payload
        except Exception as e:
            return {"ok": False, "erro": f"Falha de conexão: {e}"}

    def get(self, path: str) -> dict:
        return self.pedir("GET", path)

    def login(self) -> bool:
        print("\n  ── LOGIN ADMINISTRATIVO ──")
        print("  (necessário para ver resultados, presença e auditoria)\n")
        usuario = input("  Usuário: ").strip()
        senha = getpass.getpass("  Senha: ")
        resp = self.pedir("POST", "/api/admin/login", {"usuario": usuario, "senha": senha})
        if not resp.get("ok"):
            print(f"\n  ✗ {resp.get('erro')}")
            pausar()
            return False
        self.token = resp["sessao"]["token"]
        self.admin = resp["admin"]
        if resp.get("trocar_senha"):
            print("\n  ⚠ Este usuário ainda usa a senha inicial. Troque-a no menu local.")
        print(f"\n  ✓ Autenticado como {self.admin['nome']}")
        pausar()
        return True

    def logout(self):
        if self.token:
            self.pedir("POST", "/api/admin/logout")
        self.token = None
        self.admin = None

    def garantir_login(self) -> bool:
        return bool(self.token) or self.login()


def selecionar_eleicao(cli: Cliente) -> dict | None:
    resp = cli.get("/api/eleicoes")
    if not resp.get("ok"):
        print(f"  ✗ {resp.get('erro')}")
        pausar()
        return None
    eleicoes = resp.get("eleicoes") or []
    if not eleicoes:
        print("  Nenhuma eleição cadastrada.")
        pausar()
        return None

    print("\n  Eleições:\n")
    for e in eleicoes:
        icon = {"preparacao": "🔧", "aberta": "🟢", "fechada": "🔴"}.get(e["status"], "?")
        print(f"  [{e['id']}] {icon} {e['nome']} — {e['status'].upper()}")

    try:
        eid = int(input("\n  ID da eleição (0=cancelar): ").strip())
    except ValueError:
        return None
    if eid == 0:
        return None

    resp = cli.get(f"/api/eleicoes/{eid}")
    if not resp.get("ok"):
        print(f"  ✗ {resp.get('erro')}")
        pausar()
        return None
    return resp["eleicao"]


def mostrar_resultados(cli: Cliente, eleicao: dict):
    eid = eleicao["id"]
    resp = cli.get(f"/api/eleicoes/{eid}/resultados")
    if not resp.get("ok"):
        print(f"\n  ✗ {resp.get('erro')}")
        if resp.get("codigo") == "APURACAO_COMPROMETIDA":
            print("\n  Votos que não puderam ser abertos:")
            for f in (resp.get("falhas") or [])[:10]:
                print(f"    voto #{f.get('voto_id')}: {f.get('erro')}")
            print("\n  A apuração foi BLOQUEADA de propósito: contar esses votos")
            print("  como nulos produziria um resultado errado.")
        pausar()
        return

    r = resp["resultados"]
    limpar()
    print(f"\n  ══ RESULTADOS — {eleicao['nome']} ══")
    print(f"  Status: {eleicao['status'].upper()}\n")

    print(f"  Eleitores aptos:           {r['total_eleitores']}")
    print(f"  Compareceram (≥1 cargo):   {r['votaram']}")
    print(f"  Votaram em todos os cargos:{r['concluiram_todos_cargos']:>4}")
    print(f"  Abstenções:                {r['abstencoes']}")
    print(f"  Votos computados:          {r['total_votos']}")
    if r.get("ponderado"):
        print(f"  Peso total votos:          {r['total_peso']:.2f}")

    ponderado = r.get("ponderado")
    for cargo, lista in r.get("por_cargo", {}).items():
        ag = (r.get("agregado_cargo") or {}).get(cargo, {})
        total = ag.get("total_peso" if ponderado else "total_votos", 0)
        print(f"\n  ── {cargo.upper()} ── ({ag.get('total_votos', 0)} votos)")
        for i, c in enumerate(lista, 1):
            valor = c["peso_votos"] if ponderado else c["votos"]
            pct = (valor / total * 100) if total else 0
            if ponderado:
                print(f"  {i}º  Nº {c['numero']:02d} {c['nome']} — {c['votos']} votos / peso {c['peso_votos']:.2f} ({pct:.1f}%)")
            else:
                print(f"  {i}º  Nº {c['numero']:02d} {c['nome']} — {c['votos']} votos ({pct:.1f}%)")
        print(f"      Brancos: {ag.get('brancos', {}).get('qtd', 0)}"
              f"  |  Nulos: {ag.get('nulos', {}).get('qtd', 0)}")

    pausar()


def mostrar_relatorio_votos(cli: Cliente, eleicao: dict):
    """Relatório de presença: quem votou, sem revelar em quem."""
    eid = eleicao["id"]
    resp = cli.get(f"/api/eleicoes/{eid}/relatorio-votos")
    if not resp.get("ok"):
        print(f"  ✗ {resp.get('erro')}")
        pausar()
        return

    rel = resp["relatorio"]
    limpar()
    print(f"\n  ══ RELATÓRIO DE VOTOS — {eleicao['nome']} ══")
    print("  (Não revela em quem cada pessoa votou — sigilo preservado)\n")
    print(f"  Cargos:          {', '.join(rel.get('cargos') or []) or '—'}")
    print(f"  Total eleitores: {rel['total_eleitores']}")
    print(f"  Compareceram:    {rel['qtd_votaram']}")
    print(f"  Votação parcial: {rel['qtd_parciais']}")
    print(f"  Pendentes:       {rel['qtd_pendentes']}")
    print(f"  Votos na urna:   {rel['total_votos_computados']}")

    print(f"\n  {'STATUS':<10} {'NOME':<26} {'CPF':<16} {'UNID.':<7} {'CARGOS':<7} {'DATA'}")
    print("  " + "─" * 88)

    for e in rel["votaram"]:
        un = (e.get("unidade") or "—")[:6]
        data = (e.get("data_voto") or "")[:19]
        marca = "✓ TODOS" if e.get("completo") else "~ PARC."
        cargos = f"{e.get('cargos_votados_qtd', 0)}/{e.get('total_cargos', 0)}"
        print(f"  {marca:<10} {e['nome'][:25]:<26} {e['cpf_formatado']:<16} {un:<7} {cargos:<7} {data}")

    for e in rel["pendentes"]:
        un = (e.get("unidade") or "—")[:6]
        cargos = f"0/{e.get('total_cargos', 0)}"
        print(f"  {'— PEND.':<10} {e['nome'][:25]:<26} {e['cpf_formatado']:<16} {un:<7} {cargos:<7}")

    print("\n  [1] Salvar este relatório em CSV local")
    print("  [0] Voltar")
    if input("\n  Opção: ").strip() == "1":
        nome_arq = f"relatorio_votos_eleicao_{eid}.csv"
        import csv
        with open(nome_arq, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["STATUS", "NOME", "CPF", "UNIDADE", "BLOCO", "PESO",
                        "CARGOS_VOTADOS", "TOTAL_CARGOS", "DATA_VOTO"])
            for e in rel["votaram"]:
                w.writerow(["COMPLETO" if e.get("completo") else "PARCIAL",
                            e["nome"], e["cpf_formatado"], e.get("unidade") or "",
                            e.get("bloco") or "", e["peso"],
                            e.get("cargos_votados_qtd", 0), e.get("total_cargos", 0),
                            e.get("data_voto") or ""])
            for e in rel["pendentes"]:
                w.writerow(["PENDENTE", e["nome"], e["cpf_formatado"],
                            e.get("unidade") or "", e.get("bloco") or "", e["peso"],
                            0, e.get("total_cargos", 0), ""])
        print(f"\n  ✓ Salvo em: {os.path.abspath(nome_arq)}")
        pausar()


def mostrar_integridade(cli: Cliente, eleicao: dict):
    eid = eleicao["id"]
    resp = cli.get(f"/api/eleicoes/{eid}/integridade")
    if not resp.get("ok"):
        print(f"  ✗ {resp.get('erro')}")
        pausar()
        return

    d = resp["integridade"]
    limpar()
    print(f"\n  ══ AUDITORIA DE INTEGRIDADE — {eleicao['nome']} ══\n")
    print(f"  Registros de participação:        {d['participacoes']}")
    print(f"  Votos na urna:                    {d['votos_urna']}")
    print(f"  Eventos no log (VOTO_REGISTRADO): {d['eventos_log']}")
    print(f"  Eleitores que votaram em tudo:    {d['eleitores_completos']}")
    print()
    if d.get("consistente"):
        print("  ✓ Integridade OK — os três números batem.")
    else:
        print("  ✗ Divergência detectada!")
        print(f"    participação − urna = {d['divergencia']['participacao_vs_urna']}")
        print(f"    urna − log          = {d['divergencia']['urna_vs_log']}")

    if d.get("por_cargo"):
        print("\n  Votos por cargo:")
        for cargo, info in d["por_cargo"].items():
            print(f"    {cargo}: {info['qtd']} (peso {info['peso']:.2f})")

    timeline = d.get("timeline") or []
    if timeline:
        print(f"\n  Timeline ({len(timeline)} eventos):\n")
        for ev in timeline[:40]:
            print(f"  [{ev.get('registrado_em', '')}] {ev.get('detalhes', '')}")
        if len(timeline) > 40:
            print(f"  ... e mais {len(timeline) - 40}")
    pausar()


def mostrar_log_auditoria(cli: Cliente):
    resp = cli.get("/api/auditoria?limite=50")
    if not resp.get("ok"):
        print(f"  ✗ {resp.get('erro')}")
        pausar()
        return

    limpar()
    print("\n  ══ LOG DE AUDITORIA (últimos 50) ══\n")
    resumo = resp.get("resumo") or []
    if resumo:
        print("  Resumo por ação:")
        for a in resumo:
            print(f"    {a['acao']}: {a['total']}")
        print()

    regs = resp.get("registros") or []
    print(f"  {'ID':<6} {'DATA':<22} {'AÇÃO':<20} {'DETALHES'}")
    print("  " + "─" * 85)
    for r in regs:
        data = (r.get("registrado_em") or "")[:19]
        acao = (r.get("acao") or "")[:18]
        det = (r.get("detalhes") or "")[:40]
        print(f"  {r.get('id', ''):<6} {data:<22} {acao:<20} {det}")
    pausar()


def mostrar_quadro(cli: Cliente, eleicao: dict):
    """Quadro público de compromissos — não exige login."""
    resp = cli.get(f"/api/eleicoes/{eleicao['id']}/compromissos")
    if not resp.get("ok"):
        print(f"  ✗ {resp.get('erro')}")
        pausar()
        return
    q = resp["quadro"]
    limpar()
    print(f"\n  ══ QUADRO PÚBLICO DE COMPROMISSOS — {eleicao['nome']} ══\n")
    print(f"  Total: {q['total']}")
    print(f"  Raiz Merkle: {q['merkle_raiz']}\n")
    for c in q["compromissos"][:30]:
        print(f"  #{c['voto_id']:<5} [{(c.get('cargo') or '—')[:14]:<14}] {c['compromisso'][:40]}…")
    if q["total"] > 30:
        print(f"  ... e mais {q['total'] - 30}")
    print("\n  Os compromissos são públicos; o conteúdo do voto permanece secreto.")
    pausar()


def conferir_recibo(cli: Cliente, eleicao: dict):
    """Conferência de recibo do eleitor — não exige login."""
    print("\n  Cole o recibo (compromisso) recebido na hora do voto.")
    comp = input("  Recibo: ").strip()
    if not comp:
        return
    resp = cli.pedir("POST", "/api/recibo/verificar",
                     {"eleicao_id": eleicao["id"], "compromisso": comp})
    if not resp.get("ok"):
        print(f"\n  ✗ {resp.get('erro')}")
        pausar()
        return
    v = resp["verificacao"]
    print()
    if v.get("encontrado") and v.get("prova_valida"):
        print(f"  ✓ {v['mensagem']}")
        print(f"    Posição no quadro: {v['indice']} de {v['total_no_quadro']}")
        print(f"    Cargo: {v.get('cargo') or '—'}")
        print(f"    Registrado em: {v.get('registrado_em')}")
        print(f"    Raiz Merkle: {v['merkle_raiz']}")
    else:
        print(f"  ✗ {v['mensagem']}")
    print("\n  A conferência não revela em quem você votou.")
    pausar()


def main():
    parser = argparse.ArgumentParser(description="Terminal de Relatórios — Urna Eletrônica")
    parser.add_argument("--servidor", default="127.0.0.1", help="IP ou hostname do servidor")
    parser.add_argument("--porta", type=int, default=8080, help="Porta do servidor")
    args = parser.parse_args()
    cli = Cliente(f"http://{args.servidor}:{args.porta}")

    status = cli.get("/api/status")
    if not status.get("ok"):
        print(f"\n  ✗ Não foi possível conectar ao servidor {cli.base}")
        print(f"    {status.get('erro', '')}\n")
        sys.exit(1)

    while True:
        limpar()
        print("""
╔══════════════════════════════════════════════════════════════╗
║           TERMINAL DE RELATÓRIOS (REDE)                      ║
╚══════════════════════════════════════════════════════════════╝
""")
        print(f"  Servidor: {cli.base}")
        print(f"  Sessão:   {cli.admin['nome'] if cli.admin else 'não autenticado'}\n")
        print("  [1] Ver resultados (apuração)          * login")
        print("  [2] Relatório de votos / presença      * login")
        print("  [3] Auditoria / integridade da eleição * login")
        print("  [4] Log de auditoria do sistema        * login")
        print("  [5] Quadro público de compromissos       público")
        print("  [6] Conferir um recibo de voto           público")
        print("  [L] Entrar / trocar de usuário")
        print("  [S] Sair da sessão (logout)")
        print("  [0] Sair")
        op = input("\n  Opção: ").strip().upper()

        if op == "0":
            cli.logout()
            break
        if op == "L":
            cli.logout()
            cli.login()
            continue
        if op == "S":
            cli.logout()
            print("\n  ✓ Sessão encerrada.")
            pausar()
            continue

        if op in ("1", "2", "3", "4"):
            if not cli.garantir_login():
                continue
            if op == "4":
                mostrar_log_auditoria(cli)
                continue
            eleicao = selecionar_eleicao(cli)
            if not eleicao:
                continue
            if op == "1":
                mostrar_resultados(cli, eleicao)
            elif op == "2":
                mostrar_relatorio_votos(cli, eleicao)
            elif op == "3":
                mostrar_integridade(cli, eleicao)
        elif op in ("5", "6"):
            eleicao = selecionar_eleicao(cli)
            if not eleicao:
                continue
            if op == "5":
                mostrar_quadro(cli, eleicao)
            else:
                conferir_recibo(cli, eleicao)
        else:
            print("  Opção inválida.")
            pausar()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Encerrado.\n")
