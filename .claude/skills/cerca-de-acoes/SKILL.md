---
name: cerca-de-acoes
description: As cercas de seguranca que toda acao destrutiva do FaceOps precisa respeitar: so container do projeto compose do FindFace, nunca durante limpeza de eventos, nunca matar PID solto, dupla confirmacao e auditoria. Use sempre que for criar ou editar qualquer rota/tela que reinicie, pare, apague ou limpe alguma coisa - stack_service.py, limpeza_service.py, faxina_service.py, rotas com DELETE/POST de acao, ou quando o usuario pedir botao de parar/reiniciar/matar processo/limpar.
---

# Cercas de ação destrutiva

Docs: `docs/06_SEGURANCA.md`, `docs/18_LIMPEZA_DE_EVENTOS.md`. Se algo aqui
divergir do código, **o código manda**.

## Toda ação em container passa pela cerca do projeto

`StackService._garantir_do_projeto()` recusa agir em container que não
pertença ao projeto compose do FindFace naquele host. Sem essa cerca, um
endpoint de "reiniciar container" vira controle remoto irrestrito do
Docker — dá para derrubar o próprio painel ou o agente do Zabbix.

Nome de container também passa por allowlist antes de chegar perto do
shell.

## Nunca durante a limpeza de eventos

O manual da NtechLab é explícito: não reiniciar container do FindFace nem o
Docker durante a purga de dados — causa erro no banco. O painel **recusa**
o reinício enquanto há limpeza em andamento, e a limpeza agendada **não
começa** se houver backup em curso no mesmo host (backup completo para o
stack).

## Matar PID solto: não

A tela de Processos mostra quem consome, e o botão de ação reinicia o
**container dono do processo**, pela mesma rota cercada — não o PID. Num
servidor de reconhecimento facial, matar processo solto pode corromper
banco. A capacidade prática é a mesma; o risco, não.

## O resto do padrão

- Ação destrutiva irreversível exige confirmação digitada
  (`ConfirmarDigitando`).
- Toda ação que muda estado gera auditoria, com autor e IP; as destrutivas
  em nível `critical`.
- Limpeza pontual tem piso de idade (7 dias) imposto pelo **servidor**, não
  pela tela.
- O que sai por faxina é histórico e sobra de disco — nunca configuração,
  cadastro, artefato de backup ou auditoria crítica.
