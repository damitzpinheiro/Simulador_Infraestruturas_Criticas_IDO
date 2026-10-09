# Auditoria da bibliografia do PFC (`refs.bib`)

Analise de todas as entradas de `C:\LUCIANO\IME\5.1\PFC\Trab_Escrito\Trab_Final\refs.bib`, verificadas contra fontes primarias (IEC Webstore, IEEE, ACM DL, Springer, Elsevier, Crossref, NIST, sites/repos oficiais). Cada entrada foi cruzada com as chaves realmente citadas nos fontes `.tex` compilados.

**Escopo do que e compilado:** o `main.tex` faz `\input` apenas de `cap-introducao`, `cap-fundamentacao`, `cap-gerador`, `cap-ataque`, `cap-resultados`, `cap-conclusao`, `apendice-configs` e `exemplo-apendice`. Os arquivos `exemplo-cap-01.tex` e `exemplo-intro.tex` (que contem varias citacoes de template) **nao sao incluidos**, portanto suas citacoes nao contam. `apendice-configs.tex` e `exemplo-apendice.tex` nao possuem `\cite`.

## Resumo geral

- **24 referencias citadas** (todas do bloco "Referencias acrescentadas para o PFC").
- **40 referencias nao citadas** (restos do template abnTeX2 e do exemplo-cap-01), candidatas a remocao.
- **Correcoes de metadados nas referencias citadas: nenhuma correcao factual obrigatoria.** Todas as 24 citadas existem e seus metadados conferem com a fonte primaria (autores, ano, veiculo, volume, numero, paginas, DOI, edicao). Ha apenas 2 refinamentos opcionais (ediciao explicita em `iec62541_5`/`iec62351_5`) e observacoes de higiene BibTeX.
- Os itens "suspeitos" apontados na tarefa (`iec62541_2` ano 2026; `iec62541_6` ed. 4.0; etc.) foram verificados e estao **corretos** — inclusive o ano 2026, pois a norma foi de fato publicada em fev/2026.

## Tabela-resumo

| Chave | Citada? | Status | Nota |
|---|---|---|---|
| iec104 | Sim | OK | IEC 60870-5-104:2006+AMD1:2016 CSV, ed. 2.1, 2016-06-07 — confere |
| ieee1815 | Sim | OK | IEEE Std 1815-2012, DOI 10.1109/IEEESTD.2012.6327578 — confere |
| iec62541 | Sim | OK | IEC 62541-1:2025, 1a ed. (IS), substitui TR de 2020 — confere |
| iec62541_2 | Sim | OK | IEC 62541-2:2026, 1a ed. (IS), publicada fev/2026 — ano 2026 correto |
| iec62541_6 | Sim | OK | IEC 62541-6:2025, 4a ed., substitui 3a ed. (2020) — confere |
| nist80082 | Sim | OK | NIST SP 800-82r3 (2023), DOI 10.6028/NIST.SP.800-82r3 — confere |
| east2009taxonomy | Sim | OK | Springer, CIP III, vol. 311 (IFIP AICT), p. 67-81 — confere |
| lee2016ukraine | Sim | OK | Lee/Assante/Conway, E-ISAC+SANS, Defense Use Case, mar/2016 — confere |
| falliere2011stuxnet | Sim | OK | Falliere/Murchu/Chien, Symantec, v1.4 (fev/2011) — confere |
| pliatsios2020survey | Sim | OK | IEEE COMST v22 n3 p1942-1976 (2020), DOI confere |
| iec62351_3 | Sim | OK | IEC 62351-3:2023, ed. 2.0 — confere |
| iec62351_5 | Sim | OK | IEC 62351-5:2023 (1a ed. como IS) — ano confere; edicao pode ser explicitada |
| queiroz2011scadasim | Sim | OK | IEEE Trans. Smart Grid v2 n4 p589-597 (2011), DOI confere |
| antonioli2015minicps | Sim | OK | ACM CPS-SPC 2015, p91-100, DOI confere |
| dehlaghi2023icssim | Sim | OK | Computers in Industry, vol.148, art.103906 (2023), DOI confere |
| dahlmanns2020opcua | Sim | OK | ACM IMC 2020, p101-110, DOI confere |
| python | Sim | OK | Python Software Foundation, docs.python.org — dono/URL corretos |
| asyncua | Sim | OK | FreeOpcUa/opcua-asyncio (GitHub) — dono/URL corretos |
| scadalts | Sim | OK | SCADA-LTS/Scada-LTS (GitHub) — dono/URL corretos |
| wireshark | Sim | OK | Wireshark Foundation, wireshark.org — dono/URL corretos |
| modbus | Sim | OK | Modbus Org., App. Protocol Spec. V1.1b3 (26/04/2012) — confere |
| maynard2014mitm | Sim | OK | ICS-CSR 2014 (BCS), DOI 10.14236/ewic/ics-csr2014.5 — confere |
| radoglou2019attacking | Sim | OK | IEEE SERVICES 2019, p41-46, DOI confere |
| iec62443_3_3 | Sim | OK | IEC 62443-3-3:2013, ed. 1.0 (2013-08) — confere |
| ibge1993 | Nao | REMOVER | template abnTeX2 |
| abntex2-wiki-como-customizar | Nao | REMOVER | template abnTeX2 (so citado em exemplo-intro, nao compilado) |
| talbot2012 | Nao | REMOVER | template abnTeX2 (glossaries.sty) |
| babel | Nao | REMOVER | template abnTeX2 |
| abntex2modelo-artigo | Nao | REMOVER | template abnTeX2 |
| abntex2modelo-relatorio | Nao | REMOVER | template abnTeX2 |
| abntex2modelo | Nao | REMOVER | template abnTeX2 |
| araujo2012 | Nao | REMOVER | template abnTeX2 |
| memoir | Nao | REMOVER | template abnTeX2 |
| abntex2cite-alf | Nao | REMOVER | template abnTeX2 |
| abntex2cite | Nao | REMOVER | template abnTeX2 |
| abntex2classe | Nao | REMOVER | template abnTeX2 |
| NBR10520:2002 | Nao | REMOVER | template abnTeX2 |
| NBR6024:2012 | Nao | REMOVER | template abnTeX2 |
| NBR6028:2003 | Nao | REMOVER | template abnTeX2 |
| NBR14724:2001 | Nao | REMOVER | template abnTeX2 |
| NBR14724:2002 | Nao | REMOVER | template abnTeX2 |
| NBR14724:2005 | Nao | REMOVER | template abnTeX2 |
| NBR14724:2011 | Nao | REMOVER | template abnTeX2 |
| van86 | Nao | REMOVER | leftover; campo Author malformado |
| guizzardi2005 | Nao | REMOVER | leftover (ontologia) |
| macedo2005 | Nao | REMOVER | leftover |
| EIA649B | Nao | REMOVER | leftover |
| masolo2010 | Nao | REMOVER | leftover |
| guarino1995 | Nao | REMOVER | leftover |
| bates2010 | Nao | REMOVER | leftover |
| doxiadis1965 | Nao | REMOVER | leftover |
| dewey1980 | Nao | REMOVER | leftover |
| Salles2014 | Nao | REMOVER | so citado em exemplo-cap-01 (nao compilado) |
| Justel2014 | Nao | REMOVER | idem; note com data invalida "31 nov." |
| Goldschmidt2005 | Nao | REMOVER | idem |
| Rakocevic2014 | Nao | REMOVER | idem |
| Lara2014 | Nao | REMOVER | idem |
| Soares2013 | Nao | REMOVER | idem; note com data invalida "31 nov." |
| icse2015 | Nao | REMOVER | idem; note com data invalida "31 nov." |
| Yoko2003 | Nao | REMOVER | idem |
| Dias2013 | Nao | REMOVER | idem; tipo `@thesis` nao-padrao; note invalida |
| Araujo2015 | Nao | REMOVER | idem; tipo `@monography` nao-padrao |
| Gubitoso1992 | Nao | REMOVER | idem |
| Folha2015 | Nao | REMOVER | idem; note com data invalida "31 nov." |

---

## Referencias PFC citadas — verificadas OK

### Normas de protocolo

**iec104** — IEC 60870-5-104. Fonte: IEC Webstore, publicacao 25035 (`IEC 60870-5-104:2006+AMD1:2016 CSV`), edicao **2.1**, data 2016-06-07, 281 p., versao consolidada (2a ed. de 2006 + Emenda 1). Titulo, edicao, ano e a nota "incorpora a Emenda 1" conferem.
Fonte: https://webstore.iec.ch/publication/25035

**ieee1815** — IEEE Std 1815-2012 (DNP3). Fonte: IEEE. DOI 10.1109/IEEESTD.2012.6327578 resolve para o documento IEEE 6327578 = "IEEE Standard for Electric Power Systems Communications — Distributed Network Protocol (DNP3)", publicado 2012-10-10. Titulo, ano e DOI conferem.
Fonte: https://doi.org/10.1109/IEEESTD.2012.6327578 ; https://standards.ieee.org/ieee/1815/6177

**iec62541** (Part 1) — IEC 62541-1:2025, "OPC Unified Architecture — Part 1: Overview and concepts". 1a edicao como Norma Internacional, cancela/substitui a IEC TR 62541-1:2020. **Ano 2025 correto.** (Sem campo Edition na entrada, o que e aceitavel.)
Fonte: https://webstore.iec.ch/en/publication/ (busca IEC 62541-1:2025); confirmado tambem em distribuidores oficiais.

**iec62541_2** (Part 2, Security Model) — **Edition 1.0, Year 2026: CORRETO.** A IEC 62541-2:2026 foi publicada em fev/2026 como 1a edicao de Norma Internacional (as versoes anteriores eram Relatorios Tecnicos: IEC TR 62541-2:2016 ed.2.0 e IEC TR 62541-2:2020 ed.3.0, agora substituidos). Como a data de hoje e 30/09/2026, a norma **ja existe** — nao e "ainda nao publicada".
Fonte: https://www.vde-verlag.de/iec-normen/255905/iec-62541-2-2026.html ; https://scc-ccn.ca/standardsdb/standards/2058323

**iec62541_6** (Part 6, Mappings) — IEC 62541-6:2025, **Edition 4.0: CORRETO.** 4a edicao, cancela/substitui a 3a edicao (2020); revisao tecnica. Ano e edicao conferem.
Fonte: https://www.vde-verlag.de/iec-normen/255791/iec-62541-6-2025.html ; https://catalogue.normdocs.ru/catalog/com.normdocs.iec.card.iec.62541-6.2025.ed4.0/

**iec62351_3** — IEC 62351-3:2023, **Edition 2.0: CORRETO.** 2a edicao, cancela/substitui a 1a edicao (2014) + Amd1:2018 + Amd2:2020. Titulo, edicao e ano conferem.
Fonte: https://webstore.iec.ch/en/publication/68410

**iec62351_5** — IEC 62351-5:2023. **Ano 2023 correto.** Publicada 2023-01-13 como 1a edicao de Norma Internacional (substitui a Especificacao Tecnica IEC TS 62351-5:2013). A entrada nao declara Edition; opcionalmente adicionar `Edition = {1.0}` para precisao (e a 1a ed. como IS).
Fonte: https://webstore.iec.ch/publication/65511

**iec62443_3_3** — IEC 62443-3-3:2013, **Edition 1.0 (2013-08): CORRETO.** Titulo, edicao e ano conferem (inclui o corrigendum de abr/2014).
Fonte: https://webstore.iec.ch/publication/7033

**modbus** — Modbus Organization, "MODBUS Application Protocol Specification V1.1b3", 26/04/2012. Dono, versao (V1.1b3), ano e URL oficial conferem.
Fonte: https://www.modbus.org/modbus-specifications

### Referencias tecnicas / relatorios

**nist80082** — NIST SP 800-82r3, "Guide to Operational Technology (OT) Security", 2023. DOI 10.6028/NIST.SP.800-82r3 confere. A lista de 10 autores da entrada bate exatamente com a do registro Crossref (Stouffer, Pease, Tang, Zimmerman, Pillitteri, Lightman, Hahn, Saravia, Sherule, Thompson). Obs. minima: o titulo oficial da NIST grafa "security" em minusculo; a entrada usa "Security" (irrelevante para renderizacao).
Fonte (Crossref): https://api.crossref.org/works/10.6028/NIST.SP.800-82r3

**lee2016ukraine** — Lee, Robert M.; Assante, Michael J.; Conway, Tim. "Analysis of the Cyber Attack on the Ukrainian Power Grid", Defense Use Case, E-ISAC + SANS ICS, publicado em 18/03/2016. Autores, tipo, instituicoes, mes e ano conferem (e o "Defense Use Case #5").
Fonte: https://www.nerc.com/pa/CI/ESISAC/Documents/E-ISAC_SANS_Ukraine_DUC_5.pdf (copia em icscsi.org e nsarchive.gwu.edu)

**falliere2011stuxnet** — Falliere, Nicolas; Murchu, Liam O.; Chien, Eric. "W32.Stuxnet Dossier", Symantec, Version 1.4 (fev/2011). Autores, versao, editora e ano conferem. (Mes fev/2011; entrada nao traz Month — opcional.)
Fonte (WorldCat): https://search.worldcat.org/title/701693843

### Artigos revisados por pares (todos verificados via Crossref + DOI resolvendo ao veiculo correto)

**east2009taxonomy** — East, S.; Butts, J.; Papa, M.; Shenoi, S. "A Taxonomy of Attacks on the DNP3 Protocol". Critical Infrastructure Protection III (IFIP AICT vol. 311), p. 67-81, Springer, 2009. Autores, titulo, serie, **volume 311**, paginas e DOI 10.1007/978-3-642-04798-5_5 conferem.
Fonte: https://api.crossref.org/works/10.1007/978-3-642-04798-5_5

**pliatsios2020survey** — Pliatsios, D.; Sarigiannidis, P.; Lagkas, T.; Sarigiannidis, A. G. "A Survey on SCADA Systems: Secure Protocols, Incidents, Threats and Tactics". IEEE Communications Surveys & Tutorials, v.22, n.3, p.1942-1976, 2020. Tudo confere; DOI 10.1109/COMST.2020.2987688.
Fonte: https://api.crossref.org/works/10.1109/COMST.2020.2987688

**queiroz2011scadasim** — Queiroz, C.; Mahmood, A.; Tari, Z. "SCADASim — A Framework for Building SCADA Simulations". IEEE Transactions on Smart Grid, v.2, n.4, p.589-597, 2011. Tudo confere; DOI 10.1109/TSG.2011.2162432.
Fonte: https://api.crossref.org/works/10.1109/TSG.2011.2162432

**antonioli2015minicps** — Antonioli, D.; Tippenhauer, N. O. "MiniCPS: A Toolkit for Security Research on CPS Networks". Proceedings of the First ACM Workshop on Cyber-Physical Systems-Security and/or PrivaCy (CPS-SPC), p.91-100, ACM, 2015. Tudo confere; DOI 10.1145/2808705.2808715.
Fonte: https://api.crossref.org/works/10.1145/2808705.2808715

**dehlaghi2023icssim** — Dehlaghi-Ghadim, A.; Balador, A.; Moghadam, M. H.; Hansson, H.; Conti, M. "ICSSIM — A framework for building industrial control systems security testbeds". Computers in Industry, vol.148, art. 103906, 2023. Tudo confere; DOI 10.1016/j.compind.2023.103906.
Fonte: https://api.crossref.org/works/10.1016/j.compind.2023.103906

**dahlmanns2020opcua** — Dahlmanns, M.; Lohmoller, J.; Fink, I. B.; Pennekamp, J.; Wehrle, K.; Henze, M. "Easing the Conscience with OPC UA: An Internet-Wide Study on Insecure Deployments". Proceedings of the ACM Internet Measurement Conference (IMC), p.101-110, ACM, 2020. Tudo confere; DOI 10.1145/3419394.3423666.
Fonte: https://api.crossref.org/works/10.1145/3419394.3423666

**maynard2014mitm** — Maynard, P.; McLaughlin, K.; Haberler, B. "Towards Understanding Man-in-the-Middle Attacks on IEC 60870-5-104 SCADA Networks". 2nd International Symposium for ICS & SCADA Cyber Security Research (ICS-CSR) 2014, BCS Learning & Development, 2014. Autores, titulo, veiculo, editora, ano e DOI 10.14236/ewic/ics-csr2014.5 conferem. Paginas p.30-42 nao aparecem no registro Crossref (sem `page`), mas correspondem aos anais do BCS; manter e razoavel.
Fonte: https://api.crossref.org/works/10.14236/ewic/ics-csr2014.5

**radoglou2019attacking** — Radoglou-Grammatikis, P.; Sarigiannidis, P.; Giannoulakis, I.; Kafetzakis, E.; Panaousis, E. "Attacking IEC-60870-5-104 SCADA Systems". 2019 IEEE World Congress on Services (SERVICES), p.41-46, IEEE, 2019. Tudo confere; DOI 10.1109/SERVICES.2019.00022.
Fonte: https://api.crossref.org/works/10.1109/SERVICES.2019.00022

### Ferramentas / software (@manual) — donos e URLs oficiais corretos

- **python** — "Python Software Foundation", The Python Language Reference v3.14, https://docs.python.org/3/reference/. Dono e URL corretos. (Python 3.14 lancado em out/2025, coerente com a data do trabalho.)
- **asyncua** — "FreeOpcUa", opcua-asyncio, https://github.com/FreeOpcUa/opcua-asyncio. Repositorio/dono corretos.
- **scadalts** — "SCADA-LTS", https://github.com/SCADA-LTS/Scada-LTS. Repositorio/dono corretos.
- **wireshark** — "Wireshark Foundation", https://www.wireshark.org. Detentor atual correto (a Wireshark Foundation e a entidade mantenedora).

Observacao geral: nas entradas `@manual` de norma (iec104, ieee1815, iec62541*, iec62351*, iec62443_3_3) nao ha campo Url/Doi (exceto ieee1815 com Doi); isso e aceitavel para normas ISO/IEC, cuja referencia canonica e o numero + edicao + ano.

---

## Referencias nao citadas — candidatas a remocao (limpeza)

Nenhuma destas aparece nos capitulos compilados. Podem ser removidas do `refs.bib` sem afetar o documento.

**Restos do template abnTeX2** (documentacao do proprio template):
`ibge1993`, `abntex2-wiki-como-customizar`, `talbot2012`, `babel`, `abntex2modelo-artigo`, `abntex2modelo-relatorio`, `abntex2modelo`, `araujo2012`, `memoir`, `abntex2cite-alf`, `abntex2cite`, `abntex2classe`, `NBR10520:2002`, `NBR6024:2012`, `NBR6028:2003`, `NBR14724:2001`, `NBR14724:2002`, `NBR14724:2005`, `NBR14724:2011`.

**Restos tematicos do template** (ontologia/arquitetura da informacao — sem relacao com o PFC):
`van86`, `guizzardi2005`, `macedo2005`, `EIA649B`, `masolo2010`, `guarino1995`, `bates2010`, `doxiadis1965`, `dewey1980`.

**Entradas de exemplo** (so citadas em `exemplo-cap-01.tex`, que **nao e compilado**):
`Salles2014`, `Justel2014`, `Goldschmidt2005`, `Rakocevic2014`, `Lara2014`, `Soares2013`, `icse2015`, `Yoko2003`, `Dias2013`, `Araujo2015`, `Gubitoso1992`, `Folha2015`.

> Observacao: se em algum momento `exemplo-cap-01.tex`/`exemplo-intro.tex` forem incluidos no `main.tex`, essas chaves passariam a ser necessarias. Enquanto nao forem, sao lixo. As chaves `abnt-bibtex-doc` e `abnt-bibtex-alf-doc` sao citadas dentro de campos `Annote` de entradas de template, mas nao estao definidas no `refs.bib` — inofensivo (Annote nao gera citacao).

---

## Problemas de higiene BibTeX

1. **Tipos nao-padrao** — `@thesis` (`Dias2013`) e `@monography` (`Araujo2015`) nao sao tipos padrao do BibTeX e podem nao renderizar no estilo `abntex2cite`. Ambos sao entradas nao citadas; ao remover, o problema desaparece. (Para dissertacao/TCC o padrao seria `@mastersthesis`.)

2. **Campo Author malformado** — `van86`: `Author = {{van}, Gigch, John P. and Leo L. Pipino}`. O primeiro autor esta quebrado (deveria ser algo como `John P. van Gigch`). Entrada nao citada — remover.

3. **Datas invalidas no campo note** — `note = {31 nov. de 2015}` (novembro tem 30 dias) em `Justel2014`, `Soares2013`, `icse2015`, `Dias2013` e `Folha2015`. Todas nao citadas — remover.

4. **Datas de acesso futuras** — `python`/`asyncua`/`scadalts`/`wireshark` tem `Urlaccessdate = 28 set. 2026` e `modbus` `29 set. 2026`. Sao coerentes com a data do trabalho (30/09/2026); apenas confirmar que sao as datas reais de consulta.

5. **DOI em @manual** — `ieee1815` traz `Doi`; dependendo da versao do `abntex2cite`, o campo `Doi` pode nao ser impresso para o tipo `@manual`. Se quiser garantir a exibicao do DOI, verificar o suporte do estilo ou mover a URL para o campo `Url`.

6. **Cabecalho do arquivo** — comentario "Created for Lauro Cesar Araujo" (BibDesk) e residual do template; cosmetico.

---

## Conclusao pratica

- **Correcoes factuais obrigatorias nas referencias citadas: 0.** As 24 entradas citadas estao corretas, incluindo todas as normas IEC com anos/edicoes que pareciam suspeitos (2025/2026 sao datas reais de publicacao).
- **Refinamentos opcionais:** declarar `Edition = {1.0}` em `iec62351_5` (e, se desejar, em `iec62541` e `iec62541_2`, ambas 1a ed. como IS); considerar adicionar `Month` a `falliere2011stuxnet` (fev.) e URL/DOI a `lee2016ukraine`/`falliere2011stuxnet` se a norma de citacao exigir.
- **Limpeza recomendada:** remover as 40 entradas nao citadas (template + exemplos), o que tambem elimina os tipos nao-padrao (`@thesis`, `@monography`), o `van86` malformado e as datas "31 nov." invalidas.
