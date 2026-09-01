# Specs — o contrato do FaceOps

Três camadas de escrita neste repositório, com papéis diferentes. Não
repetem uma à outra de propósito:

| Camada | Onde | Responde | Público |
|---|---|---|---|
| **Documentação** | `docs/` | como opera, como instalar, como resolver | quem usa e quem opera |
| **Skills** | `.claude/skills/` | o que já foi aprendido na marra ao mexer no código | quem (ou o que) vai editar |
| **Specs** | `specs/` | o que precisa continuar verdadeiro, e o que ainda está aberto | quem revisa e quem decide |

Um spec aqui **não** descreve funcionalidade — para isso existe `docs/`.
Ele fixa **invariantes**: afirmações que precisam permanecer verdadeiras
enquanto o sistema evoluir, cada uma amarrada ao cenário de teste que a
trava.

## Conteúdo

- **[invariantes.md](invariantes.md)** — o contrato. Cada invariante tem
  motivo, o que acontece se quebrar, e o cenário de
  `backend/tests/verificar.py` que o defende.
- **[pendencias.md](pendencias.md)** — o que está aberto, com a evidência
  já levantada e o próximo passo concreto. É registro de investigação, não
  lista de desejos.

## Como usar

Antes de mudar comportamento, procure aqui se ele é invariante. Se for,
duas saídas honestas:

1. o invariante continua valendo e a mudança precisa respeitá-lo; ou
2. o invariante mudou de propósito — então **este arquivo e o teste mudam
   junto, no mesmo commit**, com o porquê registrado.

O que não vale é um invariante silenciosamente deixar de ser verdade.
