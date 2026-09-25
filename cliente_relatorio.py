#!/usr/bin/env python3
"""
Terminal de RELATÓRIOS em rede.

Uso:
  python3 cliente_relatorio.py --servidor 192.168.1.10
  python3 cliente_relatorio.py --servidor 192.168.1.10 --porta 8080
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error


def limpar():
    os.system("cls" if os.name == "nt" else "clear")


def pausar():
    input("\n  Pressione ENTER para continuar...")


def api(base: str, path: str) -> dict:
    url = base.rstrip("/") + path
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"ok": False, "erro": f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "erro": f"Falha de conexão: {e}"}


def selecionar_eleicao(base: str) -> dict | None:
    resp = api(base, "/api/eleicoes")
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

    resp = api(base, f"/api/eleicoes/{eid}")
    if not resp.get("ok"):
        print(f"  ✗ {resp.get('erro')}")
        pausar()
        return None
    return resp["eleicao"]


def mostrar_resultados(base: str, eleicao: dict):
    eid = eleicao["id"]
    resp = api(base, f"/api/eleicoes/{eid}/resultados")
    if not resp.get("ok"):
        print(f"  ✗ {resp.get('erro')}")
        pausar()
        return

    r = resp["resultados"]
    limpar()
    print(f"\n  ══ RESULTADOS — {eleicao['nome']} ══")
    print(f"  Status: {eleicao['status'].upper()}\n")

    print(f"  Eleitores aptos:     {r['total_eleitores']}")
    print(f"  Compareceram:        {r['votaram']}")
    print(f"  Abstenções:          {r['abstencoes']}")
    print(f"  Votos computados:    {r['total_votos']}")
    if r.get("ponderado"):
        print(f"  Peso total votos:    {r['total_peso']:.2f}")

    for cargo, lista in r.get("por_cargo", {}).items():
        print(f"\n  ── {cargo.upper()} ──")
        total = sum(c["peso_votos"] for c in lista) if r.get("ponderado") else sum(c["votos"] for c in lista)
        for i, c in enumerate(lista, 1):
            valor = c["peso_votos"] if r.get("ponderado") else c["votos"]
            pct = (valor / total * 100) if total > 0 else 0
            if r.get("ponderado"):
                print(f"  {i}º  Nº {c['numero']:02d} {c['nome']} — {c['votos']} votos / peso {c['peso_votos']:.2f} ({pct:.1f}%)")
            else:
                print(f"  {i}º  Nº {c['numero']:02d} {c['nome']} — {c['votos']} votos ({pct:.1f}%)")

    print(f"\n  Brancos: {r['brancos']['qtd']}  |  Nulos: {r['nulos']['qtd']}")
    pausar()


def mostrar_relatorio_votos(base: str, eleicao: dict):
    """Relatório de presença: quem votou, sem revelar em quem."""
    eid = eleicao["id"]
    resp = api(base, f"/api/eleicoes/{eid}/relatorio-votos")
    if not resp.get("ok"):
        print(f"  ✗ {resp.get('erro')}")
        pausar()
        return

    rel = resp["relatorio"]
    limpar()
    print(f"\n  ══ RELATÓRIO DE VOTOS — {eleicao['nome']} ══")
    print("  (Não revela em quem cada pessoa votou — sigilo preservado)\n")
    print(f"  Total eleitores: {rel['total_eleitores']}")
    print(f"  Já votaram:      {rel['qtd_votaram']}")
    print(f"  Pendentes:       {rel['qtd_pendentes']}")
    print(f"  Votos na urna:   {rel['total_votos_computados']}")

    print(f"\n  {'STATUS':<10} {'NOME':<28} {'CPF':<16} {'UNID.':<8} {'DATA'}")
    print("  " + "─" * 80)

    for e in rel["votaram"]:
        un = (e.get("unidade") or "—")[:7]
        data = (e.get("data_voto") or "")[:19]
        print(f"  {'✓ VOTOU':<10} {e['nome'][:27]:<28} {e['cpf_formatado']:<16} {un:<8} {data}")

    for e in rel["pendentes"]:
        un = (e.get("unidade") or "—")[:7]
        print(f"  {'— PEND.':<10} {e['nome'][:27]:<28} {e['cpf_formatado']:<16} {un:<8}")

    # Opção de salvar CSV local
    print("\n  [1] Salvar este relatório em CSV local")
    print("  [0] Voltar")
    op = input("\n  Opção: ").strip()
    if op == "1":
        nome_arq = f"relatorio_votos_eleicao_{eid}.csv"
        with open(nome_arq, "w", encoding="utf-8-sig") as f:
            f.write("STATUS;NOME;CPF;UNIDADE;BLOCO;PESO;DATA_VOTO\n")
            for e in rel["votaram"]:
                f.write(f"VOTOU;{e['nome']};{e['cpf_formatado']};{e.get('unidade') or ''};{e.get('bloco') or ''};{e['peso']};{e.get('data_voto') or ''}\n")
            for e in rel["pendentes"]:
                f.write(f"PENDENTE;{e['nome']};{e['cpf_formatado']};{e.get('unidade') or ''};{e.get('bloco') or ''};{e['peso']};\n")
        print(f"\n  ✓ Salvo em: {os.path.abspath(nome_arq)}")
        pausar()


def mostrar_integridade(base: str, eleicao: dict):
    eid = eleicao["id"]
    resp = api(base, f"/api/eleicoes/{eid}/integridade")
    if not resp.get("ok"):
        print(f"  ✗ {resp.get('erro')}")
        pausar()
        return

    d = resp["integridade"]
    limpar()
    print(f"\n  ══ AUDITORIA DE INTEGRIDADE — {eleicao['nome']} ══\n")
    print(f"  Eleitores que já votaram:         {d['eleitores_votaram']}")
    print(f"  Votos na urna:                    {d['votos_urna']}")
    print(f"  Eventos no log (VOTO_REGISTRADO): {d['eventos_log']}")
    print()
    if d.get("consistente"):
        print("  ✓ Integridade OK — os três números batem.")
    else:
        print("  ✗ Divergência detectada!")
        print(f"    eleitores − urna = {d['divergencia']['eleitores_vs_urna']}")
        print(f"    urna − log       = {d['divergencia']['urna_vs_log']}")

    if d.get("por_tipo"):
        print("\n  Votos por tipo:")
        for tipo, info in d["por_tipo"].items():
            print(f"    {tipo}: {info['qtd']} (peso {info['peso']:.2f})")

    timeline = d.get("timeline") or []
    if timeline:
        print(f"\n  Timeline ({len(timeline)} eventos):\n")
        for ev in timeline[:40]:
            print(f"  [{ev.get('registrado_em', '')}] {ev.get('detalhes', '')}")
        if len(timeline) > 40:
            print(f"  ... e mais {len(timeline) - 40}")
    pausar()


def mostrar_log_auditoria(base: str):
    resp = api(base, "/api/auditoria?limite=50")
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


def main():
    parser = argparse.ArgumentParser(description="Terminal de Relatórios — Urna Eletrônica")
    parser.add_argument("--servidor", default="127.0.0.1", help="IP ou hostname do servidor")
    parser.add_argument("--porta", type=int, default=8080, help="Porta do servidor")
    args = parser.parse_args()
    base = f"http://{args.servidor}:{args.porta}"

    status = api(base, "/api/status")
    if not status.get("ok"):
        print(f"\n  ✗ Não foi possível conectar ao servidor {base}")
        print(f"    {status.get('erro', '')}\n")
        sys.exit(1)

    while True:
        limpar()
        print("""
╔══════════════════════════════════════════════════════════════╗
║           TERMINAL DE RELATÓRIOS (REDE)                      ║
╚══════════════════════════════════════════════════════════════╝
""")
        print(f"  Servidor: {base}\n")
        print("  [1] Ver resultados (apuração)")
        print("  [2] Relatório de votos / presença")
        print("  [3] Auditoria / integridade da eleição")
        print("  [4] Log de auditoria do sistema")
        print("  [0] Sair")
        op = input("\n  Opção: ").strip()

        if op == "0":
            break
        if op == "1":
            eleicao = selecionar_eleicao(base)
            if eleicao:
                mostrar_resultados(base, eleicao)
        elif op == "2":
            eleicao = selecionar_eleicao(base)
            if eleicao:
                mostrar_relatorio_votos(base, eleicao)
        elif op == "3":
            eleicao = selecionar_eleicao(base)
            if eleicao:
                mostrar_integridade(base, eleicao)
        elif op == "4":
            mostrar_log_auditoria(base)
        else:
            print("  Opção inválida.")
            pausar()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Encerrado.\n")
