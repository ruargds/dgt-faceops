# Rastreio

A tela que responde a pergunta do plantão: **"tem algo comprometendo o
funcionamento agora?"**

O painel já mostrava os sintomas espalhados — disco em Manutenção, licença
em Licenciamento, container em Serviços, coleta em Monitor. Quem chega às
duas da manhã não tem tempo de visitar seis telas para descobrir que o
problema era licença vencida.

## O que é checado

| Origem | Achado | Critério |
|---|---|---|
| Licença | inválida | o NTLS marca `valid.valid = false` |
| Licença | perto de expirar | ≤ 60 dias (o mesmo limiar da interface do fabricante); crítico com ≤ 15 |
| Licença | limite estourado | uso acima do liberado |
| Licença | recurso quase no limite | ≥ 90% (o fabricante trata 90% como erro e 80% como aviso) |
| Componente | **serviço travado** | container `Up`, porta escutando, e nenhuma resposta HTTP |
| Componente | reiniciando em laço | `Restarting` no estado do container |
| Componente | `unhealthy` | healthcheck do próprio container |
| Componente | nenhum respondeu | há componente presente e zero respondendo |
| Disco | quase cheio | ≥ 90%; crítico com ≥ 95% |
| Monitor | coletor parado | última amostra há mais de 10 minutos |
| Monitor | coleta com erro | campo de erro na última amostra |
| Backup | painel nunca salvo | nenhuma execução do perfil painel com sucesso |
| Backup | sem destino ativo | zero destinos habilitados |
| Backup | última execução falhou | por servidor |
| Segurança | senha de fábrica | usuário do painel com a senha inicial |
| API | não cadastrada | sem usuário e senha da API do Face Detect |

**O achado que justifica a tela** é o *serviço travado*. `docker ps` diz
`Up`, o healthcheck não reclama, e o serviço não atende — é a falha que
some entre as telas, e a única forma de vê-la é perguntar ao componente na
porta dele. Foi para isso que a leitura dos componentes internos existe.

## Como cada achado é escrito

Quatro campos, e a falta de qualquer um foi motivo para não incluir a
checagem:

- **evidência** — o número ou a mensagem que o servidor devolveu. Sem isso
  o painel estaria pedindo fé;
- **impacto** — o que para de funcionar, em termos de operação, não de
  métrica;
- **ação** — onde clicar. Achado sem ação vira ansiedade;
- **origem** — licença, componente, disco, monitor, backup, segurança ou
  API, para separar o que é problema de quem.

## O que o rastreio não faz

- **Não age.** Nenhuma checagem reinicia serviço, apaga dado ou muda
  configuração. Diagnóstico que age sozinho é alarme de incêndio que abre a
  janela.
- **Não roda sozinho.** São duas execuções SSH por servidor. O clique é o
  consentimento — abrir a tela não custa nada ao ambiente.
- **Não afirma saúde.** Quando não há achado, a tela diz "nada nas
  checagens que o painel sabe fazer" — e não "está tudo bem". A diferença
  importa.
- **Não infere falha de dado ausente.** Servidor que não respondeu gera um
  achado sobre *isso*, não sobre o que ele teria respondido.

## Permissão

`metrics.view` para rastrear. As ações sugeridas continuam pedindo a
permissão de sempre — o rastreio aponta, quem age é a tela do assunto.
