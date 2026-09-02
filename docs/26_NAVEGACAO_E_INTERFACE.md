# Navegação, menu e interface (2026)

Registro do que mudou na casca do painel — nada de estratégia, só onde
cada peça mora e por quê. Padrões trazidos deliberadamente do InfraCore
(`dgt-infracore`, projeto irmão): mesma identidade DGT, mesmas soluções já
testadas em produção, sem reinventar o que já funciona lá.

---

## Tela inicial: Monitor

`AppShell.js` abria em **Painel**; agora abre em **Monitor**
(`useState("monitor")`). É a tela dos alertas — quem chega para o plantão
precisa ver isso antes de qualquer outra coisa. `Painel` continua existindo
como aba própria (visão por servidor: backup, disco, GPU), e o essencial
dela (servidores ativos, backups com falha, disco do painel) aparece
condensado no topo do Monitor para quem tem a permissão `hosts.view` — sem
duplicar a tela inteira.

## Números absolutos, por componente

Percentual sozinho esconde a decisão: "78,8% de memória" não diz se
sobra 1 GB ou 40 GB, e é a sobra que define se dá para esperar até
amanhã. Agora cada componente mostra o absoluto ao lado — "12,6 GB de
16,0 GB · 78,8%" — nos cartões e nos gráficos, com a unidade certa para
cada um (RAM e VRAM em GB do total da placa, disco em GB do sistema de
arquivos mais cheio, com o total do volume).

Para isso a amostra ganhou cinco números (`mem_total_mb`, `mem_usado_mb`,
`disco_total_gb`, `gpu_mem_total_mb`, `gpu_mem_usado_mb`) — ~40 bytes a
mais por linha, que em 30 dias de quatro servidores continua na casa de
poucos MB. O **modelo da placa** (`hosts.gpu_nome`, ex.: "NVIDIA
A10-12Q") ficou no host, e não na amostra: não muda de minuto em minuto,
e repeti-lo em cada linha seria texto duplicado milhares de vezes.

Colunas novas entram pelo mecanismo idempotente de sempre
(`COLUNAS_NOVAS` em `main.py`, `ADD COLUMN IF NOT EXISTS`) — instalação
existente atualiza sem migração manual.

## A faixa do topo não fala com servidor

O resumo no alto do Monitor (servidores monitorados, serviços fora do ar,
backups com falha, disco do painel) vem do **mesmo** `/api/monitor/resumo`
que a tela já consulta: banco do painel + uma leitura de disco local.
Nenhum SSH.

Isso é deliberado e vale registrar porque a primeira versão errava aqui:
ela chamava `/api/painel`, que faz `docker ps` por SSH em **cada**
servidor. Com o Monitor virando a tela inicial, abrir o painel passaria a
bater nas VMs de produção toda vez — exatamente o que o monitor contínuo
foi desenhado para não fazer.

## Navegação com contexto (`nav` / `alvo`)

O painel não tem roteador (sem URL por tela — decisão antiga, ver
[01_ARQUITETURA](01_ARQUITETURA.md)). Para um alerta poder abrir **direto**
no host/serviço certo em outra aba, sem introduzir React Router, o
`AppShell` ganhou um segundo estado além da aba ativa:

```js
const [alvo, setAlvo] = useState(null);
function nav(destino, alvoNovo) {
  setAba(destino);
  setAlvo(alvoNovo || null);
}
```

`nav("servicos", { hostId: 3, servico: "findface-video-worker" })` troca de
aba e leva o contexto junto. Cada tela de destino (`ServicosView`,
`RecursosView`, `ManutencaoView`, `ServidoresView`) recebe `alvo` como prop
e usa um `useEffect` para se posicionar sozinha (pré-selecionar o host, ou
rolar até o card certo). Mesmo mecanismo do `navContext` do InfraCore —
zero dependência nova, só um segundo `useState` no componente que já
trocava de aba.

Ver [25_INCIDENTES_E_LIMIARES](25_INCIDENTES_E_LIMIARES.md) para onde isso
é usado (atalho de alerta).

## Menu: entrada principal + submenus

**Monitor fica fora dos grupos, fixo no topo** (`PRINCIPAL` em
`AppShell.js`). É a entrada principal e a tela que abre por padrão —
enterrá-la dentro de um grupo recolhível esconderia justamente o que a
pessoa veio ver.

O resto virou submenu de verdade: seis grupos recolhíveis (Operação,
Monitoramento, Dispositivos, Ferramentas, Backup, Administração), cada um
com os itens indentados e uma guia à esquerda. Antes, "Operação" sozinho
tinha 12 itens soltos — achar "Manutenção" exigia ler a lista inteira.

Dois detalhes que evitam menu irritante:

- **O grupo da tela aberta nunca aparece fechado**, mesmo que tenha sido
  recolhido antes — barra lateral sem indicação de onde se está é pior
  que barra lateral comprida.
- **O estado recolhido fica no navegador** (`localStorage`, como tema e
  idioma): é preferência de quem olha a tela, não configuração da
  instalação. Se o `localStorage` estiver bloqueado, tudo abre — falhar
  para o lado visível.

## Responsivo: sidebar em gaveta

A barra lateral era fixa, sem nenhuma adaptação abaixo de ~960px — a tela
simplesmente esmagava. Trazido do InfraCore (`app-sidebar`/
`sidebar-backdrop`, o mesmo nome de classe que lá):

- **≥ 960px** (notebook/desktop): sidebar fixa, como sempre foi.
- **< 960px** (tablet/celular): sidebar vira painel deslizante
  (`position: fixed`, `transform: translateX(-100%)` → `translateX(0)`
  quando `.open`), com fundo escuro clicável para fechar e um botão
  hambúrguer (`IconMenu`) numa barra compacta que só existe visualmente
  nesse breakpoint (`.mobile-topbar`).
- **< 480px**: mais um ajuste de padding/título para telas de celular
  estreitas.

CSS em `styles.css`, seção "Menu em gaveta". Os grids de cartão
(`.grid-cards`, `.grid-stats`) já eram fluidos (`auto-fill`/`auto-fit`) —
não precisaram de breakpoint próprio.

## O nome do produto: FaceOps, não "DGT FaceOps"

A **plataforma** é o produto da desenvolvedora; **DGT** é a marca de quem
usa. Misturar os dois num painel que trata de autoria seria dizer que o
produto pertence ao cliente — mesmo raciocínio já escrito no InfraCore.

Na prática:

- O produto se chama **FaceOps** em toda parte que fala do produto: painel
  de autoria, título da API, banners dos scripts, documentação.
- A marca do cliente entra **em tempo de execução**, pelo whitelabel
  (`marca.js` + Configurações → `projeto.nome` e `projeto.cliente`). Uma
  instalação da DGT mostra "DGT" na tela de login porque foi configurada
  assim, não porque está no código.
- **Identificadores de infraestrutura não mudaram** e não devem mudar:
  `faceops_backend`, `faceops_frontend`, `faceops_postgres`,
  `/opt/.faceops`, o nome do repositório e o do banco. Renomear qualquer
  um deles quebra deploy, volume e backup existentes — e não traz nada.

## Tela de login

Estrutura herdada do InfraCore: logo do cliente **acima** do cartão, nome
do produto logo abaixo em tipo leve e espaçado, e o formulário sozinho no
cartão. A hierarquia fica marca → produto → ação, em vez de tudo empilhado
na mesma caixa.

A superfície escura vem do Camsync, que já era o padrão DGT para tela
operacional (registrado no cabeçalho do `styles.css`).

Detalhes que resolvem problema real:

- **Olho na senha.** O erro mais comum de primeiro acesso é senha
  temporária longa digitada errada, sem saber onde.
- **Placa clara atrás do logo.** O logo do cliente é a versão escura da
  marca e sumiria neste fundo. Inverter o logo de um cliente não é decisão
  de tema; a placa devolve contraste sem tocar nas cores dele.
- **Rodapé é a marca de quem usa** — e o gatilho de autoria mora nele.

## Autoria oculta (cinco cliques)

Mesmo padrão do InfraCore (`components/SobreOSistema.js` de lá,
replicado aqui com o mesmo nome de arquivo): o rodapé visível pertence à
marca de quem **usa** o painel (`marca.js`, cliente); a empresa que
**desenvolveu** fica a cinco cliques em 3 segundos, num canto discreto —
o rodapé do login e o selo de versão da sidebar. Não é segurança, é
discrição: a informação de autoria não se consulta no dia a dia, só
quando alguém precisa dela (dúvida de licença, auditoria).

`GatilhoDeAutoria` conta os cliques e abre `PainelDeAutoria` — nome do
produto, desenvolvedora, CNPJ, ano de direitos e a versão do bundle
carregado. Reaproveita o mesmo `REACT_APP_BUILD_STAMP` que o rodapé da
sidebar já usava para o selo de versão.

## Apelido do servidor

Nome de VM é bom para o sistema e ruim para quem está de plantão:
`vm-appserver-03` não diz onde a máquina fica. Cada servidor aceita um
**apelido**, em Servidores → Identificação, e é ele que aparece nos
cartões, nos seletores, nos alertas e no aviso do Telegram.

O nome técnico não desaparece: fica sob o apelido no cartão, em fonte
menor, e continua sendo o que a auditoria registra, o que nomeia o
diretório dos backups e a palavra que o painel pede para confirmar uma
ação destrutiva. A separação está detalhada em
[09_REGRAS_DESENVOLVIMENTO](09_REGRAS_DESENVOLVIMENTO.md#rótulo-não-é-identidade)
e travada por teste — trocar um pelo outro perderia backup em silêncio.

Apagar o apelido devolve o nome técnico. Nada é renomeado no disco.

## Alinhamento dos formulários

O botão "Adicionar destino" aparecia desalinhado da linha dos campos, e a
causa não era espaçamento: numa grade, a célula com texto de ajuda fica
mais alta que as vizinhas, então `align-items: flex-end` alinhava o botão
ao rodapé da célula mais alta, não à linha dos inputs.

A correção é estrutural — **campo com campo na grade; ação fora dela** —
com as classes `.form-linha`, `.form-acao` e `.form-ajuda`. As três estão
documentadas em
[09_REGRAS_DESENVOLVIMENTO](09_REGRAS_DESENVOLVIMENTO.md#formulário-campo-com-campo-ação-fora-da-grade).

## Campo que não podia ficar morto

O seletor de **Serviço**, nas regras de aviso, ficava desabilitado
dizendo "escolha um servidor primeiro" — inclusive com servidor já
escolhido. O motivo real era outro: o coletor ainda não tinha listado os
serviços daquele servidor (`hosts.servicos_conhecidos` vazio). Quem
escolhia ficava sem saída e sem entender por quê.

Agora há três estados, e cada um diz a verdade:

| Estado | O campo |
|---|---|
| sem servidor escolhido | seletor desabilitado — a regra vale para todos, não há o que restringir |
| servidor com serviços conhecidos | seletor com a lista real |
| servidor sem lista ainda | campo digitável, com sugestões, e a explicação de que o coletor preenche na próxima passada |

A regra geral: campo desabilitado tem que dizer o que **destravaria** ele.
Se a resposta for "esperar o coletor", isso precisa estar escrito.

## O que fica de fora deste registro

Decisões de arquitetura mais profundas (por que não há roteador, por que
`Configuracao` é chave/valor) continuam em
[01_ARQUITETURA](01_ARQUITETURA.md) — este documento é só o mapa do que
mudou na navegação e no menu em 2026, para quem for mexer aqui de novo não
precisar reconstruir o raciocínio lendo o diff.
