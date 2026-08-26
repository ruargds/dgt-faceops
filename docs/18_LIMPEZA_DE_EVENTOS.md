# Limpeza de eventos

> **Agendar** esta limpeza: em Agendamentos, escolha o tipo
> **Limpeza de eventos**. O modo recomendado é *"usar a política
> configurada na plataforma"*, que roda `cleanup --as-configured` — o
> manual descreve a opção como *"Apply config age options for events,
> counter records and clusters"*. Assim a idade mora num lugar só
> (Manutenção → Rotatividade do FindFace) e o agendamento não carrega uma
> segunda verdade.
>
> A limpeza agendada **se adia sozinha** se houver backup em andamento
> naquele servidor. O manual é explícito: *"Do not restart any FindFace
> Multi service containers or the Docker daemon while manually purging old
> data from the database as this will cause system errors!"* — e backup de
> perfil completo para o stack. Adiar custa nada; corromper o banco custa o
> ambiente.
 antigos

A ação que realmente libera disco num servidor de reconhecimento facial —
e a mais destrutiva do painel.

## Por que existe

Num servidor real deste ambiente:

```
/media/STORAGE/findface-multi/data     268 GB
└── findface-multi-legacy              242 GB   ← fotos de evento
    postgresql                         581 MB
    mongodb                            499 MB
    etcd                               123 MB
```

**90% do volume são fotos de evento.** Elas crescem com cada face
detectada e nunca param sozinhas. Backup não resolve isso — só transfere
o problema para outro disco. O que resolve é retenção de evento.

## O procedimento é do fabricante

A tela executa o comando oficial da NtechLab:

```bash
docker exec <container-legacy> /opt/findface-security/bin/python3 \
    /tigre_prototype/manage.py cleanup [opções]
```

Não é engenharia reversa nem gambiarra. É a ferramenta que a própria
NtechLab documenta para isso.

## Como usar

**Manutenção → selecione o servidor → Diagnosticar → Limpeza de eventos
antigos → Consultar opções.**

A lista de opções vem do `--help` do **próprio servidor**, não de uma
tabela minha. Isso importa: a lista muda entre versões, e uma opção
inventada faria o comando falhar inteiro — depois de o operador já ter
confirmado a limpeza.

Marque o que apagar e por quantos dias guardar. As opções marcadas como
**"libera mais"** são as que costumam dominar o volume:

| Opção | O que apaga |
|---|---|
| `face-events-max-fullframe-unmatched-age` | Quadro completo de face sem correspondência |
| `face-events-max-fullframe-matched-age` | Quadro completo de face com correspondência |
| `face-events-max-unmatched-age` | Eventos de face que não bateram com nenhum dossiê |
| `body-events-max-fullframe-unmatched-age` | Quadro completo de corpo sem correspondência |

O **quadro completo** é a imagem inteira da cena, não só o recorte do
rosto. É o que mais pesa, e normalmente o que menos se consulta depois.

### Dias, não segundos

A tela recebe **dias** e converte para segundos no comando. O parâmetro
nativo é em segundos: `432000` são 5 dias. Pedir isso ao operador seria
convite a apagar cinco anos achando que apagou cinco dias.

### Zero dias apaga tudo

`0` é aceito pelo fabricante e significa **apagar todos os registros
daquele tipo**, não só os antigos. A tela mostra aviso vermelho quando
há item com zero.

## Três proteções

**Confirmação por digitação.** É preciso digitar o nome exato do
servidor. Não é diálogo de "tem certeza?" — esses viram reflexo.

**Só Administrador.** A permissão `cleanup.run` não está no perfil
Técnico. Apagar evento de produção é decisão de quem responde pelo
ambiente.

**Trava contra reinício.** O manual da NtechLab é explícito:

> "Do not restart any FindFace Multi service containers or the Docker
> daemon while manually purging old data from the database as this will
> cause system errors!"

Enquanto uma limpeza roda, o painel **recusa** reiniciar container e
parar o stack naquele servidor, com mensagem explicando o porquê. Sem
essa trava, dois operadores em telas diferentes conseguiriam corromper o
banco sem nenhum aviso.

## O que a limpeza não faz

- **Não apaga o arquivo de vídeo** do Video Recorder — o manual é claro
  que a ferramenta remove eventos de câmera, não o arquivo de vídeo.
- **Não apaga dossiês nem listas de vigilância.** Cadastro não é evento.
- **Não tem lixeira.** Nenhum backup `essencial` recupera o que foi
  apagado aqui: o perfil essencial não leva as fotos de evento.

## Retenção sugerida

Ponto de partida para um ambiente com movimento alto:

| Tipo | Dias | Racional |
|---|---|---|
| Quadro completo sem correspondência | 7 | O que mais pesa e menos se consulta |
| Eventos sem correspondência | 30 | Raramente investigados depois de um mês |
| Quadro completo com correspondência | 30 | Tem valor probatório, mas pesa |
| Eventos com correspondência | 180 | O histórico que interessa |
| Contadores e clusters | 90 | Volume pequeno |

Ajuste conforme a exigência legal do contrato. Se houver obrigação de
guardar imagem por período determinado, **essa exigência manda** — e aí
a solução é disco maior ou *tiered storage*, não limpeza mais agressiva.

## Antes da primeira vez

1. **Faça um backup `completo`** do servidor. É o único perfil que leva
   as fotos, e é a última chance de ter uma cópia do que será apagado.
2. **Meça primeiro.** Recursos → Analisar mostra quanto cada diretório
   ocupa. Sem isso você não sabe se a limpeza vai render 200 GB ou 2 GB.
3. **Comece conservador.** Rode uma opção só, com prazo longo, e veja
   quanto liberou. Depois aperte.

## Quanto tempo leva

Em base grande, **horas**. O comando roda dentro do container, então não
depende da sessão SSH ficar aberta — o painel usa limite de 6 horas.

Durante a limpeza o sistema continua funcionando: novos eventos são
gravados normalmente. O que não pode é reiniciar container.

## Alternativa: retenção automática do FindFace

A plataforma da NtechLab tem configuração de limpeza automática de
eventos na própria interface (Configurações → Geral). **Se ela atender,
use-a** — limpeza automática contínua é melhor que faxina manual
periódica.

Esta tela existe para dois casos: quando a retenção automática não está
configurada e o disco já encheu, e quando é preciso uma limpeza pontual
mais agressiva que a política corrente.
