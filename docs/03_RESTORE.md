# Restauração

> **Se você está no meio de um incidente**, vá direto para a seção do perfil
> que você tem. O resto do documento espera.

## Antes de qualquer coisa

1. **Não apague nada ainda.** Mesmo um `data/` suspeito de corrupção pode
   ter partes aproveitáveis. Renomeie em vez de remover.
2. **Anote a versão.** O restore só é confiável para a **mesma versão**
   do FindFace Multi (2.4.1). Confira no `docker-compose.yaml` salvo.
3. **Tire um backup do estado atual**, mesmo quebrado. Se o restore der
   errado, você quer poder voltar para o "quebrado conhecido".

```bash
sudo mv /opt/findface-multi/data /opt/findface-multi/data.antes-do-restore
```

## Situação do restore automatizado

O restore pela web **não está implementado neste ciclo** — a permissão
`backups.restore` existe no catálogo e a UI a respeita, mas a execução é
manual, pelos procedimentos abaixo.

A razão é deliberada: restore sobrescreve produção. Automatizar isso sem
ensaio prévio em servidor de teste trocaria um problema (procedimento
manual) por outro pior (botão que destrói dados por engano). Ver *Fora deste
ciclo* em [SCOPE.md](../SCOPE.md).

## Obter o artefato

Pelo painel: **Backups** → localize a execução → botão de download.
Disponível se o artefato ainda estiver no disco do painel (não expirado pela
retenção, não enviado só para nuvem).

Se não estiver:

```bash
# Azure Blob
az storage blob download \
  --container-name faceops-backups \
  --name "vm-appserver/faceops_essencial_2026-08-24_02-00-00.tar.gz" \
  --file ./restore.tar.gz \
  --connection-string "<connection string>"

# Google Drive
rclone copy gdrive:FaceOps/backups/vm-appserver/faceops_essencial_2026-08-24_02-00-00.tar.gz .
```

Confira a integridade antes de usar — o `MANIFESTO.txt` e o histórico do
painel têm o SHA-256 esperado:

```bash
sha256sum restore.tar.gz
```

Extraia e leia o manifesto:

```bash
tar -xzf restore.tar.gz
cat */MANIFESTO.txt
```

## Perfil `config`

Recupera a configuração. Não recupera dado.

```bash
FF=/opt/findface-multi
cd <diretório extraído>

# 1. Parar o stack
cd $FF && sudo docker compose stop

# 2. Guardar o configs atual (não apagar)
sudo mv $FF/configs $FF/configs.antigo

# 3. Restaurar
sudo tar -xzf <extraído>/config/configs.tar.gz -C $FF/

# 4. Comparar o docker-compose salvo com o instalado ANTES de trocar.
#    A NtechLab avisa sobre isso, e com razão: versões diferentes têm
#    serviços e variáveis diferentes. Trocar às cegas quebra a subida.
diff $FF/docker-compose.yaml <extraído>/config/docker-compose.yaml

# 5. Se e somente se a comparação fizer sentido
sudo cp <extraído>/config/docker-compose.yaml $FF/

# 6. Licença
sudo cp <extraído>/config/licenca/* $FF/configs/  # confira o destino real

# 7. Subir
cd $FF && sudo docker compose up -d
```

## Perfil `essencial`

Recupera cadastros, usuários, câmeras, dossiês e **os vetores faciais**.
Não recupera as fotos originais de evento.

### 1. Configuração

Aplique primeiro os passos do perfil `config` acima (o artefato `essencial`
contém a mesma pasta `config/`).

### 2. PostgreSQL

```bash
FF=/opt/findface-multi
cd $FF

# Sobe só o banco — os outros serviços precisam ficar fora enquanto
# restauramos, senão gravam em cima do que estamos escrevendo.
sudo docker compose up -d postgresql
sleep 15

PG=$(sudo docker compose ps -q postgresql)
PGUSER=$(sudo docker exec $PG sh -c 'echo -n "$POSTGRES_USER"')
echo "usuário: $PGUSER"

# Papéis e permissões primeiro. Sem isto, o restore dos bancos falha
# com erro de permissão que parece corrupção.
sudo docker exec -i $PG psql -U "$PGUSER" -d postgres \
  < <extraído>/postgres/globals.sql

# Cada banco. --clean --if-exists derruba os objetos antes de recriar.
for dump in <extraído>/postgres/*.dump; do
  banco=$(basename "$dump" .dump)
  echo "restaurando $banco"
  sudo docker exec -i $PG pg_restore -U "$PGUSER" -d "$banco" \
      --clean --if-exists --no-owner --verbose < "$dump"
done
```

> `pg_restore` costuma emitir avisos com `--clean --if-exists` sobre
> objetos que não existiam. Isso é normal. O que importa é o código de
> saída e a ausência de `ERROR` sobre tabela ou constraint.

Se um banco não existir ainda (instalação nova):

```bash
sudo docker exec -i $PG createdb -U "$PGUSER" ffsecurity
```

### 3. Tarantool — os vetores faciais

```bash
FF=/opt/findface-multi

# Parar tudo que fala com o Tarantool
cd $FF && sudo docker compose stop

# Guardar o estado atual
sudo mv $FF/data/findface-tarantool-server \
        $FF/data/findface-tarantool-server.antigo

# Restaurar
sudo tar -xzf <extraído>/tarantool/tarantool-data.tar.gz -C $FF/data/

# Conferir dono e permissão — o container roda com uid próprio, e
# arquivo com dono errado faz o Tarantool subir vazio, sem erro claro.
sudo ls -la $FF/data/findface-tarantool-server/
sudo chown -R --reference=$FF/data/findface-tarantool-server.antigo \
        $FF/data/findface-tarantool-server

# Subir
sudo docker compose up -d
```

### 4. Conferência

```bash
cd /opt/findface-multi
sudo docker compose ps          # todos running?
sudo docker compose logs --tail 50 findface-tarantool-server
sudo docker compose logs --tail 50 findface-multi-legacy
```

Na interface do FindFace Multi:

- [ ] Login funciona com os usuários de antes
- [ ] Dossiês e listas de vigilância estão lá, com as fotos das faces
- [ ] Câmeras aparecem e voltam a conectar
- [ ] **Um rosto cadastrado é reconhecido** — este é o teste que prova que
      o Tarantool voltou. Cadastro sem vetor não reconhece ninguém.
- [ ] Eventos novos são gerados

**Se os cadastros aparecem mas ninguém é reconhecido**, o PostgreSQL voltou
e o Tarantool não. Revise o passo 3, com atenção especial ao dono dos
arquivos.

### 5. As fotos de evento

Não estão neste backup, por desenho. As consequências práticas:

- O histórico de eventos existe (está no PostgreSQL) mas as miniaturas e as
  imagens originais aparecem quebradas
- Eventos novos gravam imagem normalmente
- Não há como recuperar as antigas a partir deste artefato

Se as imagens forem necessárias, restaure do último perfil `completo`
disponível — ver abaixo.

## Perfil `completo`

Procedimento oficial da NtechLab. Recupera tudo, inclusive as fotos.

```bash
FF=/opt/findface-multi

# 1. Se for máquina nova, instale o FindFace Multi 2.4.1 pelo .run
#    da MESMA versão antes de continuar.

# 2. Parar
cd $FF && sudo docker compose stop

# 3. Configuração
sudo mv $FF/configs $FF/configs.antigo
sudo tar -xzf <extraído>/config/configs.tar.gz -C $FF/

# 4. Dados — este é o passo longo, horas em servidor grande
sudo mv $FF/data $FF/data.antigo
sudo tar -xzf <extraído>/completo/data.tar.gz -C $FF/

# 5. Comparar o compose antes de trocar
diff $FF/docker-compose.yaml <extraído>/config/docker-compose.yaml
sudo cp <extraído>/config/docker-compose.yaml $FF/   # se fizer sentido

# 6. Subir
cd $FF && sudo docker compose up -d

# 7. Acompanhar — na primeira subida o Tarantool reaplica xlogs e
#    o PostgreSQL faz recovery; leva mais que o normal.
sudo docker compose logs -f
```

Espaço: você precisa do tamanho de `data/` **duas vezes** durante o
procedimento (o antigo renomeado + o novo extraído). Confirme antes com
`df -h`.

## Restaurar em servidor diferente do original

Funciona, com atenção a três pontos:

1. **Licença.** O `ntls` costuma amarrar à máquina. Um servidor novo pode
   precisar de reativação junto à NtechLab.
2. **Endereços.** Se o IP mudou, os `configs/` restaurados apontam para o
   antigo. Revise os arquivos em `configs/` que mencionam host ou IP.
3. **GPU.** Restaurar de servidor com GPU em servidor sem GPU faz
   `findface-extraction-api` e `findface-video-worker` falharem na subida.
   É preciso ajustar o `docker-compose.yaml` para os modelos de CPU.

## Ensaio — faça antes de precisar

O único jeito de saber se o backup serve:

1. Suba uma VM de teste com FindFace Multi 2.4.1
2. Cadastre o servidor de teste no painel
3. Restaure nele o último `essencial` de produção
4. Confirme que um rosto cadastrado é reconhecido
5. Anote quanto tempo levou — esse é o seu RTO real

Faça isso uma vez agora, e depois a cada mudança de versão do FindFace.

> Backup nunca restaurado não é backup. É esperança com nome técnico.
