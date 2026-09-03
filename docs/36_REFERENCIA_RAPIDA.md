# Referência rápida — o que já custou caro, por subsistema

Uma página, para consulta antes de mexer. Cada seção aponta o documento
completo. **Se algo aqui divergir do código, o código manda.**

---

## Antes de commitar

```bash
cd backend  && python tests/verificar.py      # cenários, sem Postgres
cd backend  && python -c "import app.main"    # o build NÃO cobre isto
cd frontend && CI=true npm run build          # único jeito de validar JSX
```

Os três, sempre. Detalhe em
[09_REGRAS_DESENVOLVIMENTO](09_REGRAS_DESENVOLVIMENTO.md).

**Nenhum deles executa o evento de startup do FastAPI** — foi por ali que
um deploy caiu (ver abaixo).

---

## Deploy

```bash
cd /opt/.faceops && bash atualizar.sh
```

`/opt/.faceops` é onde a instalação real vive. `~/dgt-faceops` é exemplo
genérico de documento e **não existe** no servidor — já custou um "No such
file or directory".

| Variação | Para quê |
|---|---|
| `--verificar` | só diz se há versão nova; não altera nada |
| `--sem-build` | só código Python; não serve quando o frontend mudou |
| `--forcar` | passa por cima de backup/terminal em andamento |

### A armadilha que derrubou o painel (01/09/2026)

O `atualizar.sh` espera 80 s pelo `/api/saude` e, se não responder,
**reverte sozinho**. Foi o que aconteceu quando o modelo `Incidente`
declarou dois índices com o mesmo nome: o `create_all` da subida falhou, o
startup do FastAPI morreu junto, e nada respondeu.

O ponto: **`npm run build` passou e `import app.main` passou.** Nenhum dos
dois roda o startup. Há teste de nome duplicado no metadata desde então.

Completo em [17_ATUALIZACAO](17_ATUALIZACAO.md).

---

## Cercas de ação destrutiva

* **Toda ação em container passa por `StackService._garantir_do_projeto()`**
  — recusa agir em container fora do projeto compose do FindFace. Sem essa
  cerca, "reiniciar container" vira controle remoto irrestrito do Docker:
  daria para derrubar o próprio painel ou o agente do Zabbix. Nome de
  container também passa por allowlist antes de chegar perto do shell.
* **Nunca durante a limpeza de eventos.** O manual da NtechLab é
  explícito: não reiniciar container do FindFace nem o Docker durante a
  purga — causa erro no banco. O painel recusa, e a limpeza agendada não
  começa se houver backup em curso no mesmo host.
* **Matar PID solto: não.** A tela de Processos reinicia o *container dono
  do processo*, pela rota cercada. Num servidor de reconhecimento facial,
  matar processo solto pode corromper banco. A capacidade prática é a
  mesma; o risco, não.

[06_SEGURANCA](06_SEGURANCA.md) · [18_LIMPEZA_DE_EVENTOS](18_LIMPEZA_DE_EVENTOS.md)

---

## Monitor e incidentes

* **Host sem comunicação NÃO fecha incidente de serviço dele.** Sem
  alcançar a máquina não se sabe nada dos serviços; fechar registraria uma
  recuperação que ninguém observou.
* **Reinício em laço é variação numa janela**, nunca `RestartCount`
  acumulado: 40 reinícios em três meses seria alarme permanente.
* O ciclo faz **uma ida por servidor** e aproveita o resultado para
  amostra, incidente, alerta e catálogo de serviços.

[25_INCIDENTES_E_LIMIARES](25_INCIDENTES_E_LIMIARES.md) · [34_PESO_DO_PAINEL](34_PESO_DO_PAINEL.md)

---

## Crescimento e vazamento

* **Tendência sai da amostra que já existe.** Detectar consumo em subida
  não custa ida ao servidor — o que custa é rastrear o culpado, e por isso
  o rastreio só roda depois de a subida se confirmar em três ciclos.
* **`docker stats` já era lido e jogado fora.** A série de memória por
  container é ele, gravado. Nenhum comando novo; o custo é linha no banco,
  com cadência (5 min) e retenção (7 dias) próprias.
* **Quem é grande ≠ quem cresceu.** O Tarantool é o maior do disco desde
  sempre e não explica nada. A acusação vem da diferença entre duas
  medições com hora — vale para caminho no disco e para container.
* **Abrir usa a janela inteira; fechar usa o fim dela.** Se as duas
  decisões olhassem as mesmas 6h, uma vigilância resolvida às 14h ficaria
  acusando até as 20h.
* **`du` sem `timeout` é armadilha.** Na árvore de dados do FindFace ele
  não termina — e "não medido" é resposta; zero não é.
* **Reta perfeita não ganha "dobra a cada X".** Tempo de dobra é
  vocabulário de exponencial; usá-lo em série linear é inventar precisão.

## Diagnóstico e log

* **O painel não varre log sozinho.** Lê só de serviço com incidente
  aberto, 200 linhas, máx. 3 serviços por ciclo por host, máx. 1× a cada
  5 min por serviço. Fora disso, só no clique.
* **Serviço ≠ container.** `docker logs` precisa de
  `findface-multi-findface-video-worker-1`; o incidente guarda
  `findface-video-worker`. A tradução vem do resumo de saúde — sem ela,
  falha em silêncio.
* **Por que não há modelo de linguagem:** decisão registrada, não falta de
  tempo. Modelo pequeno o bastante para a VM do painel não conhece o
  domínio e produziria comando com cara de certo num painel que reinicia
  container de produção; a VM não tem folga (o build da atualização
  precisa de 1,5–2 GB); e a maior parte do pedido não pede modelo —
  agrupar erro é fingerprint, contar reincidência é `GROUP BY`, sugerir
  reparo em domínio estreito é catálogo curado e auditável.

[27_DIAGNOSTICO](27_DIAGNOSTICO.md)

---

## Licença e `findface-ntls`

**Uma instância por instalação**, do manual da NtechLab. Numa instalação
distribuída ela mora no appserver; dbserver, extraction e ftpserver **não
têm nada** na 3185 — arquitetura documentada, não defeito.

O painel só cobra licença do host que hospeda o NTLS, e "não sei ainda"
(lista de serviços não coletada) nunca vira "não consegui ler".

[23_MONITOR_E_CAMERAS](23_MONITOR_E_CAMERAS.md) · [24_RASTREIO](24_RASTREIO.md)

---

## Avisos no Telegram

* **O token é o segredo mais sensível daqui.** Cifrado com Fernet, **nunca
  sai por API** (a tela recebe nome do bot e impressão digital), e é
  removido de log e de erro — a URL do Telegram carrega o token no
  caminho, e traceback vira anexo de chamado.
* **Só envio, sem laço de escuta.** Chamada de saída apenas quando há
  evento. Sem dependência nova: `httpx` se houver, senão `urllib` numa
  thread.
* **Silêncio por omissão:** sem regra ligada, nada é enviado.

[28_AVISOS_TELEGRAM](28_AVISOS_TELEGRAM.md)

---

## As regras que valem para tudo

Estão em [35_CONCEITO_E_LICOES](35_CONCEITO_E_LICOES.md), com o defeito
que cada uma custou. Em uma linha cada:

1. Não afirmar o que não se leu — "não verificado" é resposta legítima.
2. Informar sem explicar não é informar: o que é, o que significa, o que
   fazer.
3. Ação sem resultado observável parece defeito.
4. Não duplicar o que já existe em outra tela.
5. Nada fica sem prazo.
6. O painel não pode pesar no que monitora.
7. Falhar fechado no que é segurança.
8. Teste que se satisfaz com comentário não guarda nada.
