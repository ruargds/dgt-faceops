# Destinos de backup

Onde os artefatos são guardados. Configurado **pela web**, não pelo
`.env` — trocar destino é operação de rotina (credencial de nuvem vence,
o cliente muda de provedor, um bucket enche) e não deveria exigir editar
arquivo e reiniciar container.

## Os três tipos

### `local` — disco do painel

Uma pasta na máquina onde o painel roda.

| | |
|---|---|
| **A favor** | Restauro imediato; sem custo; sem dependência de rede |
| **Contra** | Não protege contra perda do site. Se a máquina do painel morrer, o backup morre junto |
| **Retenção** | É o único tipo onde a limpeza automática age |

O caminho é **dentro do container**. O padrão `/data/backups` já está
mapeado no `docker-compose.yml` para o disco do host — se você apontar
para outro lugar, precisa mapear esse caminho também.

Um destino local é criado sozinho na primeira subida do painel, marcado
como padrão. Sem isso, um painel recém-instalado aceitaria disparar
backup e falharia no fim, depois de já ter copiado o artefato do
servidor — o pior momento para descobrir que falta configuração.

### `azure` — Azure Blob Storage

| Campo | Onde encontrar |
|---|---|
| Container | Você escolhe. Criado automaticamente se não existir |
| Connection string | Portal → Conta de armazenamento → Chaves de acesso |
| Camada | `Cool` é o padrão e o recomendado para backup |

Sobre as camadas: `Hot` custa mais para guardar e menos para ler;
`Cool` é o inverso e é o certo para backup; `Archive` é o mais barato,
mas a restauração leva **horas** — só use para cópia de longuíssimo
prazo que você espera nunca precisar com pressa.

Se o painel roda no Azure, o upload não sai da rede do provedor.

### `rclone` — qualquer outro provedor

Um único tipo cobre Google Drive, S3, Backblaze B2, OneDrive, SFTP,
WebDAV, Dropbox, MinIO, e dezenas de outros. É o que dá alcance externo
sem escrever um conector por provedor.

**Como configurar:**

1. Em qualquer máquina com rclone instalado, rode `rclone config` e crie
   o remote normalmente
2. Abra o `rclone.conf` gerado (`rclone config file` mostra o caminho)
3. Copie a seção do seu remote, **incluindo a linha entre colchetes**:

```ini
[gdrive]
type = drive
scope = drive
token = {"access_token":"ya29...","refresh_token":"1//0e...","expiry":"..."}
team_drive =
```

4. No painel: **Destinos → Novo destino → rclone**, cole o bloco no campo
   de configuração e informe `gdrive` como nome do remote

O bloco contém token e chave de acesso. Vai cifrado para o cofre (Fernet)
e é materializado num arquivo temporário de modo `0600` só durante o
envio, apagado logo depois — melhor que deixar um `rclone.conf`
permanente no container, que sobreviveria a um `docker cp` distraído.

**Parâmetros extras** são úteis para arquivo grande:

```
--bwlimit 20M              limita a banda, para não saturar o link
--drive-chunk-size 64M     blocos maiores no Google Drive
--s3-chunk-size 64M        idem para S3
--retries 5                mais tentativas em link instável
```

## Testar antes de confiar

O botão **Testar** grava um arquivo pequeno, confere e apaga.

Isso vale mais que validar credencial: permissão de escrita, container
inexistente e cota estourada só aparecem na hora de gravar — e descobrir
isso às 3h da manhã, no meio do backup, é a pior hora possível.

O resultado fica registrado no cartão do destino. Qualquer alteração na
configuração invalida o teste anterior e a tela avisa.

## Vários destinos na mesma execução

Selecione quantos quiser. Cada um é independente:

- Se o Azure falhar e o local funcionar, a execução termina como
  **sucesso com ressalva** — o erro do Azure fica registrado
- Só se **nenhum** destino aceitar é que a execução falha

Perder o backup por causa de uma credencial de nuvem vencida seria o pior
dos dois mundos.

**Ordem interna:** remotos primeiro, local por último. O envio local
*move* o arquivo do staging, então precisa ser o último a tocá-lo. Se
você marcar dois destinos locais, só o primeiro recebe — o segundo
aparece como ignorado.

## Destino padrão

Marcar um destino como **padrão** faz duas coisas:

1. Vem pré-selecionado ao criar backup ou agendamento
2. Um agendamento **sem nenhum destino marcado** usa os padrão na hora de
   rodar

O item 2 é intencional: assim, trocar o destino padrão vale
imediatamente para os agendamentos genéricos, em vez de deixá-los
apontando para um destino que você removeu.

Um destino em uso por algum agendamento **não pode ser removido** — o
painel recusa e lista quais agendamentos o usam. Descobrir isso de
madrugada, no log de um backup que falhou, seria pior.

## Retenção

| Tipo | Comportamento |
|---|---|
| `local` | Artefatos mais velhos que N dias são apagados |
| `azure`, `rclone` | **Nada é apagado pelo painel** |

Em nuvem, use a política de ciclo de vida do provedor. Apagar de lá por
conta própria arriscaria remover o único arquivo que sobrou de um
incidente — e o painel não tem contexto para essa decisão.

A retenção do agendamento sobrepõe a do destino, quando informada.

## Sugestão de arranjo

Para as VMs do FindFace, um ponto de partida:

| Destino | Tipo | Retenção | Para quê |
|---|---|---|---|
| Disco do painel | `local` | 30 dias | Restauro rápido do dia a dia |
| Azure — cofre frio | `azure` (Cool) | 0 | Proteção contra perda do site |
| Drive — cópia externa | `rclone` | 0 | Terceira cópia, provedor diferente |

Marque o local e o Azure como padrão. O terceiro entra só nos
agendamentos mensais do perfil `completo`.

> A regra 3-2-1 (três cópias, dois meios, uma fora do site) não é
> superstição: cada linha dessa tabela existe porque alguém já perdeu
> dados do jeito que ela previne.

## Segurança

- Connection string e configuração do rclone ficam cifradas com Fernet
  (AES-128-CBC + HMAC-SHA256), mesma derivação da `SECRET_KEY`
- Nenhum schema de saída expõe as colunas `*_enc`; a tela confirma o que
  está guardado pelo **fingerprint**
- Cadastrar, editar e remover destino gera auditoria de nível `critical`
- **Os artefatos em si não são cifrados** — contêm dossiês, vetores
  faciais e cadastros em claro. Trate o bucket com o mesmo cuidado que o
  servidor de origem: container privado, acesso restrito, e considere
  cifra em repouso do lado do provedor.
