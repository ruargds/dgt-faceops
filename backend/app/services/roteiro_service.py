"""
Roteiro de restauração — os comandos daquele artefato, naquele servidor.

O `docs/03_RESTORE.md` explica o procedimento; o manifesto dentro do
artefato diz o que ele tem. Nenhum dos dois responde a pergunta que se faz
no incidente: **"quais comandos eu digito, nesta máquina, para voltar este
backup?"**.

Este serviço junta as duas pontas. Ele lê o manifesto do artefato e o
cadastro do servidor e devolve passos com o comando exato — com o caminho
real da instalação (que no ambiente levantado é `/media/STORAGE/
findface-multi`, e não `/opt/findface-multi`), o nome do projeto compose e
os arquivos que aquele artefato realmente traz.

Três coisas que ele não faz, de propósito:

* **Não executa nada.** Restore sobrescreve produção; o painel monta o
  roteiro, quem decide e digita é gente.
* **Não inventa passo.** Só entra o que existe no artefato: se não há dump
  do Tarantool ali dentro, não há passo de Tarantool.
* **Não esconde a incompatibilidade de versão.** A base do Tarantool não é
  compatível entre versões maiores do FindFace, e o roteiro põe isso na
  frente, com as versões que estavam rodando quando o backup foi feito.
"""
import logging
import re

log = logging.getLogger("faceops.roteiro")


class RoteiroError(Exception):
    pass


def _sem_acento(texto: str) -> str:
    """
    Compara rótulo sem depender de acento.

    O manifesto é gerado por shell no servidor e já apareceu com e sem
    acento, dependendo do locale da máquina. Casar exigindo `Diretório`
    fazia o roteiro cair no caminho do CADASTRO (`/opt/findface-multi`) em
    vez do caminho real da instalação — que neste ambiente é
    `/media/STORAGE/findface-multi`. Errar o caminho num roteiro de
    restauração é pior que não ter roteiro nenhum.
    """
    import unicodedata

    return "".join(
        c
        for c in unicodedata.normalize("NFKD", texto)
        if not unicodedata.combining(c)
    )


def _campo(manifesto: str, rotulo: str) -> str:
    """Lê `Rótulo......: valor` do manifesto, com ou sem acento."""
    alvo = _sem_acento(rotulo).strip().lower()
    for linha in manifesto.splitlines():
        limpa = _sem_acento(linha)
        if ":" not in limpa:
            continue
        esquerda = limpa.partition(":")[0].rstrip(". ").strip().lower()
        if esquerda == alvo:
            # O valor volta da linha ORIGINAL, com acento e tudo.
            return linha.partition(":")[2].strip()
    return ""


def _conteudo(manifesto: str) -> list[str]:
    """Os caminhos listados na seção Conteúdo."""
    limpo = _sem_acento(manifesto)
    marca = limpo.find("Conteudo")
    if marca < 0:
        return []
    corpo = manifesto[marca:].split("Como restaurar", 1)[0]
    arquivos = []
    for linha in corpo.splitlines():
        m = re.match(r"\s*\d+\s+(\./\S+)", linha)
        if m:
            arquivos.append(m.group(1).lstrip("./"))
    return arquivos


def montar(manifesto: str, host, run, caminho_no_painel: str) -> dict:
    """
    Monta o roteiro. `host` e `run` vêm do banco; o resto, do manifesto.
    """
    perfil = _campo(manifesto, "Perfil") or (run.profile if run else "config")
    ff_dir = _campo(manifesto, "Diretório FindFace") or host.ffmulti_dir or "/opt/findface-multi"
    projeto = _campo(manifesto, "Projeto compose") or "findface-multi"
    servidor_backup = _campo(manifesto, "Servidor")
    data = _campo(manifesto, "Data")
    arquivos = _conteudo(manifesto)
    artefato = run.artifact_name if run else "backup.tar.gz"
    checksum = (run.checksum_sha256 if run else "") or ""

    tem = {
        "configs": any(a.endswith("configs.tar.gz") for a in arquivos),
        "compose": any(a.endswith("docker-compose.yaml") or a.endswith("docker-compose.yml") for a in arquivos),
        "licenca": any("/licenca/" in a or a.endswith(".lic") for a in arquivos),
        "postgres": any("/postgres" in a for a in arquivos),
        "tarantool": any("tarantool" in a for a in arquivos),
        "mongo": any("mongo" in a for a in arquivos),
        "etcd": any("etcd" in a for a in arquivos),
        "data": any(a.endswith("data.tar.gz") for a in arquivos),
    }

    trabalho = "/tmp/faceops-restore"
    passos: list[dict] = []

    def passo(titulo: str, comando: str, porque: str, cuidado: str = "") -> None:
        passos.append({
            "n": len(passos) + 1,
            "titulo": titulo,
            "comando": comando.strip(),
            "porque": porque,
            "cuidado": cuidado,
        })

    # ── 1. Levar o artefato até o servidor ─────────────────────────────
    passo(
        "Levar o artefato até o servidor",
        f"# no servidor {host.name} ({host.address}):\n"
        f"scp SEU_USUARIO@PAINEL:{caminho_no_painel} {trabalho}/\n"
        f"# ou baixe pelo painel (botão de download) e envie por scp/rsync",
        "O artefato está no disco do painel. Ele precisa estar na máquina "
        "onde vai ser restaurado — cada servidor volta com o backup que saiu "
        "dele.",
    )

    passo(
        "Conferir se o arquivo chegou íntegro",
        f"mkdir -p {trabalho} && cd {trabalho}\n"
        f"sha256sum {artefato}\n"
        f"# esperado: {checksum or '(sem checksum registrado)'}",
        "Restaurar arquivo truncado corrompe o que estava bom. Este é o "
        "checksum que o painel calculou quando o backup foi feito.",
        "Se não bater, PARE. Baixe de novo.",
    )

    passo(
        "Extrair o artefato",
        f"cd {trabalho} && tar -xzf {artefato}\nls -la",
        "O artefato é um `.tar.gz` com o manifesto e as partes dentro.",
    )

    # ── 2. Conferir a versão antes de qualquer escrita ─────────────────
    passo(
        "Conferir a versão do FindFace instalada agora",
        f"cd {ff_dir} && sudo docker compose images | head -20",
        f"O backup foi feito em {data or 'data não registrada'}, no servidor "
        f"{servidor_backup or host.name}. Compare com as versões listadas no "
        "manifesto.",
        "A base do Tarantool NÃO é compatível entre versões maiores. Se a "
        "versão mudou, os cadastros do PostgreSQL voltam, mas o "
        "reconhecimento não. Nesse caso use o procedimento de atualização do "
        "fabricante, não o restore.",
    )

    # ── 3. Config ──────────────────────────────────────────────────────
    if tem["configs"]:
        passo(
            "Guardar a configuração atual antes de sobrescrever",
            f"sudo tar -czf {trabalho}/configs-ANTES-$(date +%Y%m%d-%H%M).tar.gz "
            f"-C {ff_dir} configs",
            "Se o restore piorar as coisas, esta cópia é o caminho de volta. "
            "Leva segundos e cabe em qualquer disco.",
        )
        passo(
            "Restaurar a configuração",
            f"sudo tar -xzf {trabalho}/config/configs.tar.gz -C {ff_dir}/",
            f"Devolve `{ff_dir}/configs` ao estado do backup.",
        )

    if tem["compose"]:
        passo(
            "Comparar o docker-compose salvo com o que está no servidor",
            f"diff {trabalho}/config/docker-compose.yaml {ff_dir}/docker-compose.yaml "
            "|| true",
            "O compose define versões de imagem e portas. Diferença aqui "
            "explica comportamento estranho depois do restore.",
            "Não sobrescreva sem olhar: o compose atual pode ter ajustes "
            "feitos depois do backup.",
        )

    if tem["licenca"]:
        passo(
            "Conferir os arquivos de licença",
            f"ls -la {trabalho}/config/licenca/",
            "O artefato guarda os `.lic`. Eles só precisam voltar se a "
            "licença tiver sido perdida junto — o NTLS lê de "
            "`/ntech/license/`.",
        )

    # ── 4. Bancos (perfil essencial) ───────────────────────────────────
    if tem["postgres"]:
        passo(
            "Restaurar os bancos PostgreSQL",
            f"ls {trabalho}/postgres/\n"
            f"# para CADA instância e CADA .dump encontrado:\n"
            f"sudo docker exec -i $(sudo docker ps --filter "
            f"label=com.docker.compose.project={projeto} "
            f"--filter label=com.docker.compose.service=postgresql "
            f"--format '{{{{.Names}}}}' | head -1) \\\n"
            f"  pg_restore --clean --if-exists -U ffsecurity -d NOME_DO_BANCO "
            f"< {trabalho}/postgres/INSTANCIA/NOME_DO_BANCO.dump",
            "O perfil essencial traz um `pg_dump` por banco, por instância "
            "(postgresql e timescaledb são instâncias diferentes).",
            "`--clean` apaga o conteúdo atual do banco antes de recriar. É o "
            "que se quer num restore, e é irreversível.",
        )

    if tem["tarantool"]:
        passo(
            "Restaurar os vetores faciais (Tarantool)",
            f"cd {ff_dir} && sudo docker compose stop $(sudo docker compose ps "
            f"--services | grep tarantool)\n"
            f"# extrair o snapshot sobre o diretório de dados do Tarantool e "
            f"subir de novo:\n"
            f"cd {ff_dir} && sudo docker compose start $(sudo docker compose ps "
            f"--services | grep tarantool)",
            "Os vetores são o que faz o reconhecimento funcionar. Sem eles, "
            "os dossiês voltam mas ninguém é reconhecido.",
            "Os shards precisam estar PARADOS durante a substituição dos "
            "arquivos. Subir com arquivo pela metade corrompe a base.",
        )

    if tem["mongo"]:
        passo(
            "Restaurar o MongoDB",
            f"sudo docker exec -i $(sudo docker ps --filter "
            f"label=com.docker.compose.project={projeto} --format "
            f"'{{{{.Names}}}}' | grep -i mongo | head -1) \\\n"
            f"  mongorestore --archive --gzip --drop "
            f"< {trabalho}/mongodb/mongodump.gz",
            "Guarda parte dos metadados da aplicação.",
            "`--drop` remove as coleções atuais antes de restaurar.",
        )

    if tem["etcd"]:
        passo(
            "Restaurar o etcd",
            f"# com o etcd parado:\n"
            f"sudo docker exec -i $(sudo docker ps --filter "
            f"label=com.docker.compose.project={projeto} --format "
            f"'{{{{.Names}}}}' | grep -i etcd | head -1) \\\n"
            f"  etcdctl snapshot restore /caminho/no/container/snapshot.db",
            "O etcd guarda a coordenação entre os componentes de vídeo.",
            "Restaurar snapshot de etcd exige o serviço parado e recria o "
            "diretório de dados.",
        )

    # ── 5. Completo ────────────────────────────────────────────────────
    if tem["data"]:
        passo(
            "Procedimento oficial do perfil completo",
            f"# 1. instalar o FindFace Multi da MESMA versão pelo .run\n"
            f"cd {ff_dir} && sudo docker compose stop\n"
            f"sudo rm -r {ff_dir}/configs/* && sudo tar -xzf "
            f"{trabalho}/config/configs.tar.gz -C {ff_dir}/\n"
            f"sudo rm -r {ff_dir}/data/* && sudo tar -xzf "
            f"{trabalho}/data.tar.gz -C {ff_dir}/\n"
            f"cd {ff_dir} && sudo docker compose up -d",
            "É o procedimento da NtechLab para o perfil completo: instalação "
            "limpa da mesma versão, depois configs e data por cima.",
            "PARA o FindFace inteiro e apaga `configs/` e `data/` atuais. Só "
            "em janela de manutenção, e só com a mesma versão instalada.",
        )

    # ── 6. Subir e conferir ────────────────────────────────────────────
    passo(
        "Subir o stack",
        f"cd {ff_dir} && sudo docker compose up -d",
        "Sobe o que estiver parado. O que já estava rodando não é tocado.",
    )
    passo(
        "Conferir",
        f"cd {ff_dir} && sudo docker compose ps\n"
        f"# e no painel: Serviços, Rastreio e a tela do FindFace",
        "Container rodando não é o mesmo que serviço atendendo — o Rastreio "
        "do painel pergunta a cada componente na porta dele.",
    )

    return {
        "perfil": perfil,
        "servidor": host.name,
        "endereco": host.address,
        "ff_dir": ff_dir,
        "projeto": projeto,
        "artefato": artefato,
        "checksum": checksum,
        "feito_em": data,
        "feito_no_servidor": servidor_backup,
        "conteudo": arquivos,
        "tem": tem,
        "trabalho": trabalho,
        "passos": passos,
        "aviso_versao": (
            "A base do Tarantool não é compatível entre versões maiores do "
            "FindFace. Confira as versões no manifesto antes de restaurar."
        ),
    }
