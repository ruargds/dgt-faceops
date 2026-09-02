# Pendências

O que está aberto, com a evidência já levantada e o próximo passo
concreto. Registro de investigação — quando fechar, sai daqui e vira
documentação ou entrada em `docs/10_ERROS_CONHECIDOS.md`.

---

## P-1 — Câmeras sem evento: achar a via certa

**Situação:** as 200 câmeras apareciam como "sem evento". A causa
imediata era o painel afirmar sem saber, e isso **já foi corrigido** — a
tela agora mostra o motivo e escreve "não verificada". Falta a causa raiz:
por que a leitura de eventos falha.

**Evidência levantada:**

- A varredura reportava `0 eventos varridos` e `1 requisição à API` — só a
  página de câmeras. Toda chamada de evento falhou.
- Os caminhos usados (`/events/faces/`, `/bodies/`, `/cars/`) conferem com
  o log real do FindFace (`POST /events/faces/add/ 200`, registrado em
  `docs/10_ERROS_CONHECIDOS.md`).
- Suspeita principal: a versão da API recusa o parâmetro `ordering`. O
  painel agora tenta de novo sem ele, então parte dos casos pode já estar
  resolvida.
- O usuário confirma que **existem passagens com imagem vinculadas aos
  dispositivos** — o dado existe; falta o caminho.

**Próximo passo:** abrir Licenciamento e dispositivos → Última interação
depois da atualização e ler a mensagem de erro por tipo, que agora aparece
na tela. Ela diz se é parâmetro, permissão do token ou caminho.

**Alternativa se a API não servir:** ler direto do banco do FindFace, como
`dispositivos_service` já faz para câmeras. Atenção: lá as consultas de
evento usam `created_date` e `camera_id` fixos, com `2>/dev/null` — se a
coluna tiver outro nome nesta versão, o resultado é silêncio, não erro.
Descobrir as colunas com `_colunas()` (que já existe) antes de assumir.

---

## P-2 — Descobrir o `chat_id` do grupo pelo painel

**Situação:** configurar o Telegram exige pegar o id do grupo por fora
(adicionar o bot, mandar mensagem, chamar `getUpdates`). O passo de
adicionar o bot ao grupo é manual e **não tem como automatizar** — o
Telegram não permite. O resto tem.

**Próximo passo:** botão "descobrir grupos" que chama `getUpdates` com o
token já salvo e lista os chats onde o bot está, para escolher em vez de
digitar. Backend pequeno (`telegram_service` já tem o transporte).

---

## P-3 — Invariantes ainda sem trava automática

Sete invariantes de `invariantes.md` dependem de revisão de código:
INV-5, INV-6, INV-8, INV-12, INV-13, INV-14, INV-19.

Os mais baratos de cobrir:

- **INV-12/13** (cerca do projeto compose): teste com `StackService` falso
  confirmando que ação em container de outro projeto é recusada.
- **INV-19** (fronteira de erro): teste de render com componente que
  levanta, confirmando que o menu sobrevive — exige lib de teste de React,
  que o projeto não tem. Avaliar se compensa.
- **INV-5** (sem SSH novo no ciclo): contar chamadas de `ssh.run*` num
  ciclo com serviços falsos e travar o número.

---

## P-4 — Processos: layout e leitura

**Situação:** GPU por processo, serviço dono e botão de reinício já entram
na tabela. O pedido de "aproveitar melhor os espaços" foi atendido em
parte — a tabela ganhou colunas, mas o cabeçalho de resumo e a densidade
não foram revistos.

**Próximo passo:** revisar a tela inteira com a régua de 1080p, como foi
feito no Monitor.

---

## P-5 — Modelo local para explicar erro desconhecido

**Situação:** decidido **não** ter modelo de linguagem no caminho quente
(ver `docs/27_DIAGNOSTICO.md` para o raciocínio completo: precisão em
domínio estreito, e a VM do painel não comporta sem quebrar o build do
`atualizar.sh`).

O único ponto onde um modelo ganharia é explicar em português um erro que
o catálogo **não** reconhece.

**Condições para reabrir:** rodar fora da VM do painel, sob demanda (nunca
no ciclo), com teto de memória e timeout, e sem permissão para gerar
comando executável.

## P-6 — Redirecionamento pode driblar a cerca de SSRF

`core/rede_segura.validar_url` valida o endereço CADASTRADO. O cliente
HTTP segue redirecionamento (`follow_redirects=True`), então um servidor
legítimo poderia responder com redirect para `169.254.169.254`.

Fechar exige validar cada salto — trocar por redirecionamento manual com
validação a cada passo, ou desligar redirect e ver o que quebra. Não foi
feito agora porque instalação existente pode depender do redirect (barra
final, HTTP para HTTPS).

**Risco real:** baixo. Exige `hosts.manage`, e quem tem essa permissão já
tem SSH com sudo nos quatro servidores.

## P-7 — Segundo fator

O que reduz de verdade o valor de uma senha roubada. Hoje há freio de
força bruta, senha em bcrypt e sessão com expiração, mas quem tiver a
senha entra.

TOTP é o caminho: sem dependência de SMS, sem custo, e o `pyotp` é
pequeno. Precisa de tela de cadastro com QR, códigos de recuperação e
decisão sobre exigir por perfil ou para todos.

## P-8 — Sair do `react-scripts`

`npm audit` acusa 31 alertas, todos de dependência transitiva do
`react-scripts` que roda em tempo de BUILD — nenhuma vai para o
navegador. O pacote está abandonado.

Migrar para Vite resolve os 31 de uma vez e acelera o build. Porte médio:
mexe em `index.html`, variáveis de ambiente (`REACT_APP_` → `VITE_`) e
scripts do `package.json`. `npm audit fix --force` quebraria o build sem
ganho de segurança real.
