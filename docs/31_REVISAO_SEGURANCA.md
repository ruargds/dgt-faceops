# Revisão de segurança — 02/09/2026

Varredura completa em busca do que pode quebrar, ser invadido ou causar
dano. Este documento registra **o que foi verificado**, não só o que foi
corrigido: saber que uma classe de ataque foi olhada e está coberta vale
tanto quanto a correção.

---

## Corrigido nesta revisão

### 1. A chave de exemplo subia em produção — CRÍTICO

`SECRET_KEY` tinha o valor `dev-only-trocar` no código e
`troque-esta-chave` no `.env.example`, **sem nenhuma verificação na
subida**. Uma instalação que copiasse o exemplo e esquecesse de trocar
rodaria com uma chave pública.

O dano não é só sessão: essa mesma chave **deriva a chave Fernet do
cofre**. Com ela, qualquer pessoa que leia o repositório assina um token
de administrador *e* decifra as chaves SSH dos quatro servidores de
produção.

Agora o painel **recusa subir** com placeholder ou chave abaixo de 32
caracteres, e a mensagem ensina a gerar uma. Em `MODO_DEV` só avisa.

> Falhar fechado aqui custa uma subida. Um painel de pé com a chave de
> exemplo é pior que um fora do ar: ele parece funcionar.

**Trava:** `chave fraca impede a subida`

### 2. `python-jose` 3.3.0 → PyJWT

Duas falhas conhecidas sem correção publicada:

| CVE | O que é | Havia mitigação? |
|---|---|---|
| CVE-2024-33663 | confusão de algoritmo com chave ECDSA OpenSSH | sim, por acaso — a lista `algorithms=[...]` já era passada |
| CVE-2024-33664 | negação de serviço por token JWE inflado | **não** — o painel decodifica token de quem chama em toda requisição |

A troca não invalida sessão: mesmo formato HS256. E a lista de algoritmos
aceitos passou a ser **fechada no código**, não vinda de
`settings.ALGORITHM` — configuração errada não pode abrir a porta, e
`alg: none` ali viraria token forjável.

Também passou a exigir `exp` e `sub`: token sem expiração é senha que
nunca muda.

**Trava:** `jwt nao aceita algoritmo trocado`

### 3. SSRF para o serviço de metadados do Azure

`ff_api_url` é endereço escolhido por quem cadastra, e o painel faz
requisição para lá. As VMs são do Azure, e **todo Azure responde em
`169.254.169.254`** com o IMDS, que entrega token de identidade
gerenciada para quem perguntar, sem autenticação.

Agora há cerca (`core/rede_segura.py`): esquema só http/https, sem
usuário:senha embutidos, e recusa link-local, loopback, reservado e os
nomes de metadados das nuvens — inclusive quando o nome **resolve** para
um desses endereços.

Rede privada continua liberada de propósito: é onde os servidores do
Face Detect vivem.

**Limite honesto:** o cliente HTTP segue redirecionamento, e um servidor
legítimo poderia redirecionar para o IMDS. Fechar isso exigiria mudar a
política de redirect, o que pode quebrar instalação que depende dela.
Registrado em `specs/pendencias.md`.

**Trava:** `url da api nao alcanca o metadados`

### 4. Os estáticos eram servidos sem nenhum cabeçalho de segurança

Armadilha do nginx que passa despercebida: **`add_header` dentro de um
`location` descarta todos os `add_header` do bloco `server`.** O
`location /static/` definia só o `Cache-Control` — e com isso todo JS e
CSS do painel era servido **sem CSP, sem `nosniff` e sem
`X-Frame-Options`**.

Os cabeçalhos foram repetidos dentro do location. Os demais blocos
(`/api/`, os WebSockets e `/`) não declaram `add_header` próprio, então
herdam corretamente.

---

## Verificado e já correto

Registrado para a próxima revisão não refazer o caminho:

| Área | Situação |
|---|---|
| XSS | nenhum `dangerouslySetInnerHTML`, `eval` ou `innerHTML` no frontend; React escapa por padrão |
| CSP | `script-src 'self'` **sem** `unsafe-inline` — o build usa `INLINE_RUNTIME_CHUNK=false` justamente para isso |
| SQL injection | tudo por ORM; o único `text()` com f-string monta DDL a partir de constantes do código, sem entrada de usuário |
| Força bruta | freio por (IP, usuário) e por IP, com bloqueio de 15 min |
| Enumeração de usuário | mensagem única para usuário inexistente e senha errada |
| Documentação da API | `/api/docs` e `/api/openapi.json` só existem em `MODO_DEV` |
| CORS | só `localhost:3000`, e só em modo dev |
| TLS | apenas 1.2 e 1.3; nenhuma porta HTTP exposta |
| WebSocket do terminal | ticket de uso único, não `?token=<jwt>` — JWT em query string vai para o log de acesso do nginx |
| Injeção de comando | nome de container validado por regex e passado por `shlex.quote`; caminhos do compose idem |
| Cerca do Docker | `_garantir_do_projeto` recusa agir em container fora do projeto do Face Detect — inclusive o próprio painel |
| Travessia de diretório | `caminho_artefato` e o download de gravação resolvem e conferem o prefixo |
| Upload de logo | tipo detectado pelos bytes iniciais, não pelo `content-type` do navegador; teto de 2 MB |
| Segredo em resposta | colunas `*_enc` nunca em schema de saída; só impressão digital |
| Segredo em log | `audit_service._limpar` omite chave sensível, inclusive aninhada; token do Telegram removido de toda mensagem de erro |
| Pinagem de host SSH | chave fixada por varredura explícita **antes** de qualquer autenticação — sem isso, um MITM captura a senha de sudo |

**Travas:** `segredo nunca sai em resposta nem em log`

---

## Aceito com ressalva

### Token em `localStorage`

Fica em `localStorage`, não em cookie `httpOnly`. O compromisso é
consciente e herdado do InfraCore: cookie exigiria proteção contra CSRF
em toda rota que muda estado, e o painel usa cabeçalho `Authorization`,
que é imune a CSRF por construção.

A defesa contra roubo por XSS é a **CSP sem `unsafe-inline`**: um XSS
que não executa não lê `localStorage`.

### Sobre "não passar senha pelo F12"

Vale ser direto, porque a intenção é boa e a solução intuitiva não
funciona:

* **Em trânsito, a senha já é criptografada.** O painel só atende HTTPS
  (TLS 1.2/1.3). Ninguém na rede vê a senha — nem no Wi-Fi, nem no
  provedor, nem na Cloudflare.
* **O F12 mostra o que o próprio usuário digitou, na máquina dele.** Isso
  vale para qualquer site do mundo, inclusive banco: o navegador precisa
  ter a senha em claro para poder cifrá-la no TLS.
* **Cifrar no JavaScript antes de enviar não resolve** — é a falácia
  clássica de "crypto no cliente". A chave teria de estar na página, que
  a mesma pessoa lê no mesmo F12; e o valor cifrado passaria a *ser* a
  senha para o servidor, o que não melhora nada. O que se ganha é
  aparência, não segurança.

O que **de fato** protege está feito e foi conferido nesta revisão:
senha só trafega no corpo de um POST sobre TLS (nunca em URL, que iria
para o log do nginx), nunca é registrada em log ou auditoria, nunca volta
em resposta de API, e é guardada só como hash bcrypt.

Se o objetivo for reduzir o valor da senha em si, o caminho real é
**segundo fator** — está em `specs/pendencias.md`.

### Dependências do frontend

`npm audit` acusa 31 alertas (14 altos). **Todos** vêm de dependências
transitivas do `react-scripts` que rodam **em tempo de build** (`svgr`,
`css-select`, `bfj`, plugins do webpack). Nenhuma vai para o navegador.

A correção real é migrar de `react-scripts` (abandonado) para Vite —
trabalho de porte médio, sem urgência de segurança, registrado em
`specs/pendencias.md`. Corrigir com `npm audit fix --force` quebraria o
build sem ganho real.

---

## Como repetir esta revisão

```bash
cd backend && python tests/verificar.py     # inclui as travas de segurança
cd frontend && npm audit
```

As travas de segurança fazem parte da suíte normal — não é preciso
lembrar de rodar nada à parte.
