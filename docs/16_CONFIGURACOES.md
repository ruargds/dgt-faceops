# Configurações

Aba **Configurações**. Ajustes do painel editáveis pela web, que valem na
hora — nada reinicia.

## Como funciona

Três camadas, nessa ordem de precedência:

```
banco  >  variável de ambiente (.env)  >  padrão do catálogo
```

O `.env` continua servindo para o que precisa existir **antes do banco
subir**: `SECRET_KEY`, `POSTGRES_PASSWORD` e `PORTA_HTTP`. Todo o resto
passou para a tela.

Só existe linha no banco para o que foi **alterado**. Quem está no padrão
não ocupa espaço — o que deixa óbvio, olhando o banco, o que a equipe
realmente ajustou.

## O catálogo

O catálogo é **hardcoded** em `backend/app/services/config_service.py`,
como as permissões. Adicionar uma opção é **uma linha**:

```python
ItemConfig("backup.retencao_essencial", "backup",
           "Retenção do perfil Essencial (dias)", "numero",
           30, "0 = nunca apagar.", minimo=0, maximo=3650,
           env="RETENTION_ESSENCIAL_DAYS"),
```

A tela se monta sozinha a partir disso: rótulo, tipo de campo, validação,
texto de ajuda e botão de restaurar padrão. **Nada muda no frontend.**

Foi feito assim de propósito. Opção que só existe no banco vira campo
órfão que ninguém sabe para que serve, e ter que editar duas pontas para
cada campo novo garante que uma delas fica para trás.

### Tipos

| Tipo | Campo na tela | Validação |
|---|---|---|
| `texto` | caixa de texto | até 512 caracteres |
| `numero` | campo numérico | `minimo` e `maximo` |
| `booleano` | caixa de seleção | — |
| `escolha` | lista suspensa | tem que estar em `opcoes` |

## O que dá para ajustar

### Identidade do projeto

Nome do painel, subtítulo e cliente. Aparecem na tela de login e no topo.

**É o que permite reusar a instalação em outro projeto** sem tocar em
código. O campo *cliente* é especialmente útil quando a mesma equipe
opera mais de um ambiente — evita agir no lugar errado.

### Padrões dos servidores

Diretório de instalação e diretório de trabalho no servidor. O caminho de
instalação é detectado sozinho no teste de conexão; isto aqui é só o
palpite inicial.

O **diretório de trabalho** importa: é onde o artefato é montado antes de
vir para o painel. Aponte para um disco com folga.

### Backup

Retenção por perfil, tempo limite de execução e a **margem de disco
exigida no perfil Completo** — o percentual do tamanho de `data/` que
precisa estar livre no servidor antes de começar. Foto JPEG comprime
pouco; 60% é a margem que evita encher o disco de produção de madrugada.

Precedência da retenção: agendamento > destino > este padrão.

### Logs ao vivo

Linhas iniciais, limite de linhas por segundo e teto de linhas na tela.
Servem para a aba não travar com container que despeja milhares de linhas
por segundo.

### InTerminal

Gravação de sessão e queda por inatividade. Desligar a gravação não é
recomendado: ela é o que torna um terminal web aceitável em produção.

### Manutenção

Teto do journald e tamanho de rotação do syslog, usados quando você
aplica a contenção de log. O manual da NtechLab sugere 3 GB para o
journald.

### Monitoramento contínuo

Além do intervalo do coletor e das retenções, é aqui que fica a régua da
vigilância de consumo crescente (ver
[38_CRESCIMENTO_E_VAZAMENTO](38_CRESCIMENTO_E_VAZAMENTO.md)):
janela analisada, horizonte da projeção, subida mínima que vale atenção
(uma por hora para memória, uma por dia para disco), quantos ciclos
confirmam antes de avisar, e se o rastreio do culpado roda sozinho ou só
no clique. A série de memória por container tem cadência e retenção
próprias — é a que desenha o gráfico de "quem está comendo a RAM".

### Sessão e acesso

Duração do login e o percentual de disco que deixa o cartão do servidor
vermelho no Painel.

## Permissões

| Ação | Permissão |
|---|---|
| Ver a configuração | `hosts.view` |
| Alterar | `users.manage` |

Alterar gera registro de auditoria com as chaves modificadas.

## Restaurar padrão

Todo campo que fugiu do padrão mostra um link com o valor original.
Clicar remove a linha do banco — volta a valer o `.env` ou o padrão do
catálogo.

## Tema e idioma ficam no navegador

**Tema claro/escuro** e **idioma** estão no rodapé da barra lateral, ao lado
do usuário — e **não** nesta aba, de propósito. São preferências de quem
olha a tela, não da instalação: duas pessoas no mesmo painel podem querer
coisas diferentes, e um plantão noturno não deveria depender de alguém com
permissão de configuração para escurecer a tela.

Ficam no `localStorage` do navegador. Trocar de máquina recomeça no padrão:
tema do sistema operacional e idioma do navegador.

O tema muda só os tokens de cor — nenhuma tela sabe em que tema está. A
barra lateral e o terminal seguem escuros nos dois: superfície escura em
tela operacional é o padrão DGT, e mudar isso seria trocar a identidade em
vez de acrescentar uma opção.

Sobre o idioma, o estado é este, sem enfeite: **o esqueleto do painel**
(menu, títulos de tela, rodapé, login, componentes comuns) está em
português e inglês; **o miolo das telas** — tabelas, avisos e as
explicações longas — segue só em português e vai sendo traduzido tela a
tela. Trocar o idioma **recarrega a página**, também de propósito: com
parte dos textos ainda literal, meia tradução ao vivo ficaria pior que a
pausa de meio segundo. Sessão de terminal aberta cai junto.

## O que NÃO fica aqui

Por decisão, não por esquecimento:

- **`SECRET_KEY`, `POSTGRES_PASSWORD`, `PORTA_HTTP`** — precisam existir
  antes do banco subir. Ficam no `.env`.
- **Credenciais de acesso** — chave PEM, senha de sudo, connection string
  do Azure, configuração do rclone. Ficam no cofre cifrado, nas telas de
  **Servidores** e **Destinos**, onde o fluxo de cadastro e o teste de
  conexão fazem sentido junto.

Misturar segredo com preferência numa tela só seria conveniente e errado:
são coisas com ciclo de vida, auditoria e risco diferentes.

## Usar em outro projeto

A instalação é genérica. Para atender outro cliente:

1. **Configurações → Identidade**: nome, subtítulo e cliente
2. **Servidores**: cadastre os servidores daquele ambiente
3. **Destinos**: os destinos de backup daquele cliente
4. **Configurações → Padrões**: ajuste caminhos e retenções conforme o caso

Nada disso exige tocar em código, editar `.env` ou reconstruir imagem.
