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

## Menu em grupos menores

"Operação" tinha 12 itens soltos, sem hierarquia. Virou 4 grupos —
Operação, Monitoramento, Dispositivos, Ferramentas — mesmo mecanismo de
sempre (`{ grupo: "menu.chave" }` como divisor na lista, sem submenu
aninhado). É o mesmo padrão raso que o InfraCore usa: grupo é só um
título, não uma dobra — mais fácil de escanear, sem estado de
aberto/fechado para gerenciar.

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

## O que fica de fora deste registro

Decisões de arquitetura mais profundas (por que não há roteador, por que
`Configuracao` é chave/valor) continuam em
[01_ARQUITETURA](01_ARQUITETURA.md) — este documento é só o mapa do que
mudou na navegação e no menu em 2026, para quem for mexer aqui de novo não
precisar reconstruir o raciocínio lendo o diff.
