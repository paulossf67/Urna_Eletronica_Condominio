# 🗳️ Urna Eletrônica v4.0

Sistema de urna eletrônica para **Condomínios, Associações e Sindicatos**.

- **Um voto por cargo** — o eleitor vota uma vez para Síndico, uma vez para Conselheiro, etc.
- **Sigiloso** — o voto é cifrado e não guarda nenhuma referência ao eleitor
- **Verificável** — cada eleitor recebe um recibo e pode conferir que o voto entrou na urna
- **Íntegro** — voto duplo é impossível, inclusive com várias cabines simultâneas
- CPF validado pelo algoritmo oficial
- Banco **SQLite** em modo WAL, 1 servidor + terminais de votação e relatório

---

## Instalação

```bash
pip install -r requirements.txt     # cryptography + reportlab
python main.py                      # modo local
```

Requer Python 3.10 ou superior.

### Teste rápido

```bash
python main.py
```

1. **[3]** Criar eleição de demonstração
2. Login `admin` / `admin123` → o sistema **exige a troca da senha**
3. **[4]** Abrir votação
4. Menu principal → **[2]** Área de Votação → CPF `123.456.789-09`
5. Vote para **Síndico** e depois para **Conselheiro** — guarde os dois recibos
6. Menu principal → **[4]** confira um recibo no quadro público

---

## Modelo de votação

Uma eleição tem um ou mais **cargos**, definidos pelos candidatos cadastrados.
O eleitor emite **um voto por cargo**, cada um com seu próprio recibo.

```
Eleição "Síndico 2026"
├── Cargo: Síndico       → cédula 1 → voto + recibo
└── Cargo: Conselheiro   → cédula 2 → voto + recibo
```

O controle de duplicidade usa a tabela `participacao`, com restrição
`UNIQUE(eleicao_id, eleitor_id, cargo)`. A inserção dela e a do voto acontecem
na **mesma transação `BEGIN IMMEDIATE`**: duas requisições simultâneas do mesmo
eleitor resultam em exatamente um voto, sem janela de corrida.

A tabela `votos` **não tem coluna alguma que aponte para o eleitor** — a
participação é pública, a escolha não.

---

## Segurança

| Camada | Implementação |
|---|---|
| Senha de admin | PBKDF2-HMAC-SHA256, 240.000 iterações, salt por usuário |
| Força bruta | 5 tentativas por usuário / 5 min, depois bloqueio temporário |
| Senha inicial | `admin123` **exige troca no primeiro acesso** |
| Sessão da API | Token aleatório de 256 bits; só o **hash** é guardado; 8 h de validade |
| Voto em repouso | Fernet (AES-128-CBC + HMAC-SHA256), chave em `urna.key` |
| Integridade | HMAC-SHA256 por voto |
| Compromisso | SHA-256 sobre (tipo, candidato, **cargo**, peso, nonce) |
| Quadro público | Árvore de Merkle + prova de inclusão por recibo |
| Voto nominal | Prova OR de Schnorr (Cramer–Damgård–Schoenmakers) |
| Apuração cifrada | Paillier aditivo — só o total é aberto |
| Voto remoto | Desafio → sessão HMAC → nonce único por requisição |

### Rotas autenticadas

Desde a v4.0, as rotas que expõem dados pessoais ou resultados **exigem login**:

| Rota | Acesso |
|---|---|
| `/api/status`, `/api/eleicoes`, `.../candidatos`, `.../cargos` | público |
| `.../compromissos`, `/api/recibo/verificar` | público (só hashes) |
| `.../eleitores`, `.../relatorio-votos`, `.../resultados`, `.../integridade` | **admin** |
| `/api/auditoria` | **admin** |

```bash
# 1. Login → token
curl -X POST http://IP:8080/api/admin/login \
     -H 'Content-Type: application/json' \
     -d '{"usuario":"admin","senha":"..."}'

# 2. Usar o token
curl http://IP:8080/api/eleicoes/1/resultados \
     -H 'Authorization: Bearer <TOKEN>'
```

### Arquivos sensíveis — nunca versione

```
urna.key              ← sem ela os votos NÃO podem ser apurados
urna_paillier.json    ← chave privada da apuração homomórfica
urna_schnorr.json     ← parâmetros do grupo Schnorr
urna_remoto.secret    ← segredo HMAC das sessões remotas
urna.db               ← banco
```

Todos já estão no `.gitignore`. **Faça backup de `urna.key` separadamente do
banco**: o backup do `.db` não inclui a chave, e sem ela a apuração é impossível.

### ⚠️ Tamanho das chaves

Chaves Paillier novas são geradas com **2048 bits**. Se você tem um
`urna_paillier.json` antigo de 512 bits (demonstração), apague-o **antes de
abrir a eleição** para gerar uma nova — o menu `[9] Banco de Dados` avisa
quando a chave atual é curta demais.

```bash
# Gera chave nova de 2048 bits na próxima abertura
rm urna_paillier.json

# Ou, para estudo/testes rápidos:
URNA_PAILLIER_BITS=512 python main.py
```

---

## Apuração à prova de erro silencioso

Se algum voto não puder ser decifrado (chave perdida, banco restaurado sem a
chave correspondente, corrupção), a apuração **é bloqueada** e o sistema diz
exatamente quais votos falharam. Antes da v4.0 esses votos viravam "nulo" em
silêncio, produzindo um resultado errado sem aviso.

```python
obter_resultados(eleicao_id)                  # levanta ErroApuracao
obter_resultados(eleicao_id, estrito=False)   # apura o resto e reporta as falhas
```

---

## Arquitetura para 10 máquinas

| Qtd | Função | Comando |
|-----|--------|---------|
| 1 | Servidor | `python servidor.py` |
| 2 | Relatórios | `python cliente_relatorio.py --servidor IP` |
| 7 | Votação | `python cliente_votacao.py --servidor IP` |

```bash
# Servidor
python servidor.py

# Cabines de votação
python cliente_votacao.py --servidor 192.168.1.10

# Mesas de relatório (pedem login)
python cliente_relatorio.py --servidor 192.168.1.10
```

### Rede em produção

O servidor HTTP **não cifra o canal**. Sempre coloque TLS ou VPN na frente:

```bash
# Escuta só em localhost, atrás de um proxy TLS (Caddy, Nginx)
python servidor.py --host 127.0.0.1 --porta 8080

# Se algum navegador for consumir a API, libere a origem explicitamente
python servidor.py --cors https://urna.meucondominio.local
```

---

## Banco de dados SQLite

| Item | Detalhe |
|------|---------|
| Arquivo | `urna.db` (criado automaticamente) |
| Modo | WAL + `busy_timeout` de 30 s (suporta várias cabines) |
| Biblioteca | `sqlite3` (nativa do Python) |
| Backups | Pasta `backups/` (até 20 cópias, feitas via `Connection.backup`) |
| Relatórios | Pasta `relatorios/` |
| Log | `urna.log` |

### Tabelas

```
urna.db
├── administradores      # PBKDF2, flag de troca de senha
├── eleicoes             # inclui contadores homomórficos
├── candidatos           # cada cargo distinto vira uma cédula
├── eleitores
├── participacao         # eleitor × cargo (UNIQUE) — barra o voto duplo
├── votos                # cifrado + compromisso + prova OR; sem link ao eleitor
├── sessoes_admin        # hash do token da API
├── sessoes_remotas      # sessões de voto remoto (sobrevivem a reinício)
├── desafios_remotos
├── nonces_remotos       # anti-replay persistente
├── tentativas_auth      # rate limiting
└── auditoria
```

### Escolher outro caminho para o banco

```bash
export URNA_DB=/caminho/para/meu_banco.db
python main.py

# Ou no servidor
python servidor.py --db /caminho/para/meu_banco.db
```

### Linha de comando

```bash
python database.py inspecionar          # ver tabelas e registros
python database.py backup               # criar backup agora
python database.py restaurar <arquivo>  # restaurar um backup
```

Backups automáticos acontecem ao **abrir** ou **fechar** uma votação, ao
**iniciar o servidor**, antes de **excluir uma eleição** e antes de
**restaurar** outro backup.

---

## Administração

O menu do administrador (`main.py` → `[1]`) cobre:

- **Eleições** — criar, editar, excluir (bloqueada se já houver votos)
- **Candidatos** — criar, editar, excluir; com votos na urna, apenas desativa
- **Eleitores** — criar, editar, excluir, lote manual e **importação de CSV**
- **Votação** — abrir / fechar; reabrir **não** zera os contadores cifrados
- **Resultados** — tela, CSV, PDF e quadro público de compromissos
- **Banco** — backup, **restauração** e inspeção
- **Administradores** — criar, redefinir senha, desativar
- **Auditoria** — log, integridade, provas ZK, provas OR, apuração homomórfica

### Importar eleitores de CSV

```csv
nome;cpf;unidade;bloco;peso
Carlos Mendes;123.456.789-09;101;A;1.0
Fernanda Lima;234.567.890-92;202;A;1,5
```

Aceita `;` ou `,`, cabeçalho em qualquer ordem, com ou sem acento/maiúsculas.
Linhas com erro são reportadas uma a uma sem interromper a importação.
Use `[3] → [4]` para gerar um modelo.

---

## Testes

```bash
pip install pytest
python -m pytest tests -q
```

124 testes cobrindo CPF, cifra, compromissos, Merkle, Schnorr OR, Paillier,
voto por cargo, **voto duplo sob concorrência**, apuração ponderada, apuração
estrita, senhas, rate limit, sessões, anti-replay, importação CSV e
backup/restauração.

---

## Variáveis de ambiente

| Variável | Padrão | Uso |
|---|---|---|
| `URNA_DB` | `urna.db` | Caminho do banco |
| `URNA_KEY` | `urna.key` | Chave Fernet dos votos |
| `URNA_PAILLIER` | `urna_paillier.json` | Chaves Paillier |
| `URNA_PAILLIER_BITS` | `2048` | Bits de chaves Paillier novas |
| `URNA_REMOTO_SECRET` | `urna_remoto.secret` | Segredo HMAC das sessões remotas |

---

## Licença

MIT — veja [LICENSE](LICENSE). Uso livre para fins educacionais, condominiais
e associativos.
