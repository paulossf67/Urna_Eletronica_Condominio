# 🗳️ Urna Eletrônica v3.0 — SQLite + Rede Local

Sistema de urna eletrônica para **Condomínios, Associações e Sindicatos**.

- Banco de dados: **SQLite** (`urna.db`)
- Rede local: 1 servidor + terminais de votação e relatório
- CPF validado pelo algoritmo oficial

## Banco de dados SQLite

| Item | Detalhe |
|------|---------|
| Arquivo | `urna.db` (criado automaticamente) |
| Biblioteca | `sqlite3` (nativa do Python) |
| Backups | Pasta `backups/` (até 20 cópias) |

### Tabelas

```
urna.db
├── administradores
├── eleicoes
├── candidatos
├── eleitores
├── votos
└── auditoria
```

### Escolher outro caminho para o banco

```bash
# Variável de ambiente
export URNA_DB=/caminho/para/meu_banco.db
python3 main.py

# Ou no servidor
python3 servidor.py --db /caminho/para/meu_banco.db
```

### Backup e inspeção

**Pelo menu (admin):** opção **[9] Banco de Dados**

**Pela linha de comando:**

```bash
python3 database.py inspecionar   # ver tabelas e registros
python3 database.py backup        # criar backup agora
```

Backups automáticos são feitos:
- Ao **abrir** ou **fechar** uma votação
- Ao **iniciar o servidor** de rede

## Arquitetura para 10 máquinas

| Qtd | Função | Comando |
|-----|--------|---------|
| 1 | Servidor | `python3 servidor.py` |
| 2 | Relatórios | `python3 cliente_relatorio.py --servidor IP` |
| 7 | Votação | `python3 cliente_votacao.py --servidor IP` |

## Como usar (rede)

```bash
# Servidor
python3 servidor.py

# Cabines de votação
python3 cliente_votacao.py --servidor 192.168.1.10

# Mesas de relatório
python3 cliente_relatorio.py --servidor 192.168.1.10
```

## Modo local (uma máquina)

```bash
python3 main.py
```

1. **[3]** Criar eleição de demonstração  
2. Login `admin` / `admin123`  
3. Abrir votação  
4. Votar com CPF válido, por exemplo: `123.456.789-09`

## Arquivos

```
urna_eletronica/
├── main.py              # Modo local
├── servidor.py          # Servidor de rede
├── cliente_votacao.py   # Terminal de votação
├── cliente_relatorio.py # Terminal de relatórios
├── database.py          # SQLite + backup + inspeção
├── eleicao.py           # Regras + CPF + relatórios
├── urna.db              # Banco (criado na 1ª execução)
├── backups/             # Cópias de segurança
└── README.md
```

## Segurança

- Senha admin com hash SHA-256  
- Voto anônimo  
- Controle de duplicidade por CPF (algoritmo oficial)  
- Backup automático em momentos críticos  
- Log de auditoria  

Uso livre para fins educacionais, condominiais e associativos.

## Votação remota segura

Fluxo do cliente (`cliente_votacao.py`):

1. **Desafio** `POST /api/remoto/desafio` — anti-replay (2 min)
2. **Autenticar** `POST /api/remoto/autenticar` — CPF + desafio → token HMAC (5 min, 1 uso)
3. **Votar** `POST /api/remoto/votar` — token + nonce único

Proteções:
- Sessão de uso único (não reenvia o mesmo token)
- Nonce anti-replay
- Desafio consumido na autenticação
- CPF validado + já votou

### Rede em produção

O servidor HTTP sozinho **não** cifra o canal. Use:

```bash
# Exemplo: Caddy ou Nginx com TLS na frente
# ou VPN WireGuard entre máquinas e o servidor
python3 servidor.py --host 127.0.0.1 --porta 8080
```

Arquivos sensíveis no servidor: `urna.key`, `urna_paillier.json`, `urna_remoto.secret`, `urna.db`.
