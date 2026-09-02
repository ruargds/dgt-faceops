# Sessão e perfis — quem entra, por quanto tempo, e o que pode

---

## A sessão tem dois relógios

Um só não resolve. Guardar apenas o prazo de inatividade daria sessão
eterna a quem ficasse mexendo; guardar apenas o prazo absoluto mataria
alguém no meio do trabalho.

| Relógio | Padrão | O que faz | Onde se ajusta |
|---|---|---|---|
| **Inatividade** | 20 min | sem uso, a sessão cai | Configurações → Sessão e acesso |
| **Teto absoluto** | 24 h | contado do login, **nunca se estende** | idem |

O token carrega os dois: `exp` (a janela curta) e `ini` (o instante em
que a sessão começou). Na renovação, o `ini` é **copiado**, nunca
recalculado — é isso que faz o teto ser um teto, e não um horizonte que
se afasta a cada renovação.

Passadas as 24 h é preciso entrar de novo, mesmo com uso contínuo.

### A armadilha que quase inverteu a regra

**O painel se atualiza sozinho a cada 10 segundos.** Se a renovação
acontecesse a cada requisição — que é o jeito mais natural de escrever
isso — o próprio polling seguraria a sessão viva para sempre, e o tempo
de inatividade **nunca chegaria ao fim**. Uma tela esquecida aberta na
madrugada ficaria logada indefinidamente.

Por isso a renovação depende de **evento de entrada do navegador**, não
de tráfego de rede: clique, tecla, rolagem e toque.

`mousemove` ficou de fora de propósito. Mesa esbarrada, tela em parede e
mouse com deriva manteriam uma sessão privilegiada aberta sem ninguém
ali — que é exatamente o cenário que a regra existe para cobrir.

### Antes de derrubar, avisa

A um minuto do fim aparece um aviso com botão **continuar conectado**.
Encerrar sem avisar perde o que estava sendo digitado, e quem perde um
formulário pela metade passa a desconfiar do painel inteiro.

Continuar renova a janela de inatividade — **não** o teto de 24 h.

E a tela de login diz **por que** a sessão caiu. Voltar ao login sem
explicação faz a pessoa achar que o painel quebrou, e tentar de novo no
mesmo minuto.

### Onde a regra é aplicada de verdade

O teto é conferido em **toda requisição autenticada**, não só na
renovação. Regra de segurança que depende de o cliente pedir não é
regra — bastaria um script não chamar `/auth/renovar` para nunca expirar.

Token da versão anterior, sem o carimbo `ini`, **não** derruba quem já
estava dentro: a próxima renovação passa a carregá-lo.

**Travas:** `sessao cai parada e tem teto`

---

## Os quatro perfis

| Perfil | Resumo | Para quem | O que **não** pode |
|---|---|---|---|
| **Observador** | enxerga tudo, não muda nada | gestor, auditor, cliente acompanhando; também é o perfil seguro para tela em parede | nenhuma ação: não reinicia, não baixa backup, não abre terminal |
| **Operador de plantão** | destrava o que travou | quem atende o chamado de madrugada | deixar serviço parado, restaurar backup, mexer em cadastro, usar sudo |
| **Técnico** | opera e mantém, sem poder de destruição | quem cuida do ambiente no dia a dia | restaurar ou apagar backup, parar o stack, cadastrar servidor, gerenciar usuário |
| **Administrador** | tudo, inclusive o que não tem volta | responsável pelo painel; devem existir poucos | nada é bloqueado — por isso todo destrutivo pede confirmação digitada |

A distinção que mais importa na prática é **operador × técnico**:
reiniciar um serviço volta sozinho; **parar** deixa parado. Um
`findface-video-worker` desligado é reconhecimento facial fora do ar sem
gerar erro nenhum — ninguém percebe, porque não há falha, só ausência.
Por isso `services.power` é do técnico para cima, e `services.restart` já
é do plantão.

### Por que os perfis são fixos

Perfil editável por tela pareceria flexibilidade e seria uma porta: quem
pudesse editar perfil se concederia `terminal.sudo` sem passar por
ninguém, e a auditoria registraria apenas "perfil alterado".

Com perfil fixo, conceder poder é **trocar o perfil de alguém** — uma
ação, um registro, um responsável.

Se aparecer um quinto papel de verdade, ele entra em
`core/permissions.py`, com revisão de código — não numa tela às três da
manhã.

### A escada é crescente

Cada perfil contém tudo do anterior. Há teste para isso: se um dia
deixar de valer, tem de ser decisão deliberada, e não efeito colateral
de mexer numa lista.

---

## A tela

**Usuários → O que cada perfil pode.** Quatro cartões (resumo, para quem,
o que não pode, quantas ações destrutivas) e a matriz completa agrupada
em sete áreas, expandida uma de cada vez.

Vinte e três permissões de uma vez é uma parede, e parede ninguém lê. A
pergunta real de quem cadastra alguém é *"essa pessoa vai poder mexer em
backup?"*, não *"essa pessoa tem `backups.restore`?"*.

Cada linha diz **o que a permissão faz na prática** — `backups.restore`
não conta a ninguém que aquilo sobrescreve o banco de produção — e marca
as destrutivas.

A matriz vem inteira de `/auth/perfis`, montada do **mesmo catálogo que
autoriza**. Se a tela tivesse a própria cópia, ela diria uma coisa e o
servidor faria outra, e a divergência apareceria só quando alguém
reclamasse de um botão que sumiu. Há teste comparando célula por célula
com `permissions_for()`.

Aberta a qualquer usuário autenticado: saber o que o **próprio** perfil
permite não é informação privilegiada, e esconder isso só gera a
pergunta "por que não aparece o botão?" — que vira chamado.

### Regra da interface

Botão sem permissão é **omitido**, não desabilitado. Botão cinza que não
faz nada gera chamado de suporte; botão ausente não gera dúvida.

---

## Verificação

`python tests/verificar.py`:

| Cenário | Trava |
|---|---|
| `sessao cai parada e tem teto` | renovação carrega o `ini`; teto vence mesmo com `exp` válido; conferido em toda requisição; renovação não vem de tráfego de fundo; `mousemove` fora |
| `perfis descrevem o que cada um pode` | toda permissão com área e explicação; todo perfil com para-quem e não-pode; matriz batendo célula a célula com quem autoriza; escada crescente; observador sem nenhuma ação; plantão sem destrutiva |

Verificado por injeção: fazer a renovação recalcular o `ini` e dar
`backups.restore` ao plantão — os dois testes falharam, nomeando o
problema.
