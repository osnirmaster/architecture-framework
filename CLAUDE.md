# Corporate Architecture Twin — Claude Code Instructions

@AGENTS.md
@rules.md

## Missão

Construir e manter um **digital twin file-based da arquitetura corporativa**, compilado a partir de evidências versionadas em repositórios GitHub e classificado pelo metamodelo corporativo inspirado nos factsheets LeanIX.

O sistema é um compilador de conhecimento, não um chatbot de documentação. Toda saída deve ser reproduzível, rastreável e passível de validação determinística.

## Hierarquia das fontes de verdade

1. **Repositórios e commits Git**: verdade sobre implementação técnica existente.
2. **`catalog/factsheets.json`**: verdade sobre identidade, nome e classificação dos objetos corporativos já governados no LeanIX.
3. **`config/metamodel.json` e `config/relationship-policy.json`**: verdade sobre tipos, atributos e relações permitidas.
4. **Evidências extraídas**: fatos observáveis derivados dos arquivos dos repositórios.
5. **Claims e propostas produzidas por LLM**: hipóteses revisáveis; nunca são verdade por si só.
6. **`dist/graph/graph.json`**: projeção compilada e publicada; nunca deve ser editada manualmente.

Em conflito, fontes de menor prioridade não podem sobrescrever fontes de maior prioridade. Registre o conflito e abstenha-se.

## Fluxo obrigatório

```text
sources -> evidence -> claims -> semantic proposals -> fitness engine
        -> deterministic harness -> accepted | quarantine | rejected
        -> graph snapshot -> static UI
```

1. Leia `config/repositories.json` e determine o escopo do repositório e commit.
2. Use o orquestrador da camada adequada e seus subagentes.
3. Grave apenas artefatos intermediários em `workspace/`.
4. Preserve provenance para cada fato, claim, nó e relação.
5. Valide o DSL com `python3 scripts/fitness_engine.py validate-definitions`.
6. Execute `python3 scripts/harness.py validate`; o harness executará as fases configuradas em `config/fitness-policy.json`.
7. Corrija erros bloqueantes nas propostas ou na implementação; não relaxe fitness functions para fazer a validação passar.
8. Execute `python3 scripts/harness.py publish` somente quando solicitado ou como etapa final explícita.
9. Revise `workspace/harness/fitness-results.json`, `workspace/harness/diagnostics.json` e `dist/graph/diff.json`.

## Comandos principais

```bash
python3 scripts/fitness_engine.py validate-definitions
python3 scripts/fitness_engine.py evaluate --graph dist/graph/graph.json --phase RUNTIME
python3 scripts/harness.py validate
python3 scripts/harness.py publish
python3 scripts/harness.py check
python3 -m unittest discover -s tests -v
python3 -m http.server 8080
```

Após publicar, a UI estática poderá ler `dist/graph/graph.json`.

## Limites de escrita

- Agentes podem criar ou alterar arquivos em `workspace/`, `config/`, `fitness/definitions/`, `fitness/examples/`, `schemas/`, `site/`, `tests/` e `scripts/` quando a tarefa exigir.
- Agentes não aprovam waivers; podem apenas criar propostas ou validar documentos já aprovados.
- Agentes **não podem** editar diretamente `dist/graph/**`.
- Agentes **não podem** alterar `catalog/factsheets.json` para encaixar uma inferência. Esse arquivo deve vir de exportação governada do LeanIX.
- Apenas `scripts/harness.py publish` pode materializar o snapshot publicado.

## Critério de conclusão

Uma tarefa está concluída apenas quando:

- todas as entidades e relações possuem provenance;
- nenhuma classificação foi inventada fora do metamodelo;
- ambiguidades relevantes foram colocadas em quarentena;
- fitness functions aplicáveis foram executadas e resultados UNKNOWN/STALE receberam enforcement explícito;
- a validação determinística terminou sem erros bloqueantes;
- o diff do grafo foi produzido e resumido;
- arquivos gerados não contêm segredos, tokens ou dados pessoais desnecessários.
