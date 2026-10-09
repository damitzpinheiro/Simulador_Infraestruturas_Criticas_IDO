# Novas Referências Sugeridas para a Bibliografia — PFC

Levantamento de referências acadêmicas/técnicas de **alto valor** que **ainda não constam** de `refs.bib` (Trab_Final) e que agregariam ao escopo do trabalho (gerador de tráfego para IEC 60870-5-104, DNP3, OPC-UA e Modbus TCP + superfície de ataque MITM para validação de defesas em SCADA/OT).

- Data da pesquisa: 29 set. 2026.
- **Nenhum arquivo `.tex` ou `.bib` foi alterado.** Este é apenas um relatório de sugestões.
- Cada metadado foi conferido contra a fonte primária indicada. Onde não foi possível confirmar um campo, isso está **marcado explicitamente**.
- Convenção de chave adotada: `autor+ano+palavra` (como em `east2009taxonomy`, `dahlmanns2020opcua`) para papers; sigla curta para normas (como `iec104`, `nist80082`).

---

## Tabela-resumo priorizada

| # | Chave sugerida | Referência (curta) | Tema | Onde encaixa | Prioridade |
|---|----------------|--------------------|------|--------------|------------|
| 1 | `maynard2014mitm` | Maynard & McLaughlin, MITM em IEC 60870-5-104 (2014) | Ataque a protocolo | Cap. Ataque (MITM IEC104) | **Alta** |
| 2 | `radoglou2019attacking` | Radoglou-Grammatikis et al., Attacking IEC-60870-5-104 (2019) | Ataque a protocolo | Cap. Ataque + Fundamentação | **Alta** |
| 3 | `iec62443_3_3` | IEC 62443-3-3:2013 (requisitos de segurança de sistemas) | Norma / framework | Cap. Fundamentação (segurança OT) | **Alta** |
| 4 | `cherepanov2017industroyer` | Cherepanov (ESET), Win32/Industroyer (2017) | Caso real | Cap. Introdução / Fundamentação | **Alta** |
| 5 | `eset2022industroyer2` | ESET Research, Industroyer2 (2022) | Caso real (IEC-104) | Cap. Introdução / Ataque | **Alta** |
| 6 | `bsi2022opcua` | BSI, OPC UA Security Analysis (2022) | Segurança de protocolo | Cap. Fundamentação (OPC-UA) | **Alta** |
| 7 | `radoglou2022dataset` | Radoglou-Grammatikis et al., IEC-104 IDS Dataset (2022) | Testbed / dataset | Cap. Resultados / Metodologia | Média |
| 8 | `mathur2016swat` | Mathur & Tippenhauer, SWaT testbed (2016) | Testbed / dataset | Cap. Fundamentação (testbeds) | Média |
| 9 | `fireeye2017triton` | Mandiant/FireEye, TRITON/TRISIS (2017) | Caso real | Cap. Introdução / Fundamentação | Média |
| 10 | `jicha2016conpot` | Jicha, Patton & Chen, análise do Conpot (2016) | Ferramenta / honeypot | Cap. Fundamentação / Trabalhos relacionados | Média |
| 11 | `iec101` | IEC 60870-5-101 (base do 104) | Norma | Cap. Fundamentação (IEC104) | Média |
| 12 | `iec62264` | IEC 62264-1 / ISA-95 (níveis de automação) | Norma | Cap. Fundamentação (modelo Purdue) | Baixa |

---

## 1. Ataques e análises de segurança de protocolo

### 1.1 `maynard2014mitm` — MITM em IEC 60870-5-104 (PRIORIDADE ALTA)

```bibtex
@inproceedings{maynard2014mitm,
    Author = {Maynard, Peter and McLaughlin, Kieran and Haberler, Berthold},
    Title = {Towards Understanding Man-in-the-Middle Attacks on {IEC} 60870-5-104 {SCADA} Networks},
    Booktitle = {Proceedings of the 2nd International Symposium on ICS \& SCADA Cyber Security Research (ICS-CSR)},
    Pages = {30--42},
    Publisher = {BCS},
    Year = {2014},
    Doi = {10.14236/ewic/ICSCSR2014.5}}
```

- **Onde encaixa:** Cap. Ataque, seção do MITM em IEC104 — é a referência acadêmica mais diretamente alinhada à superfície de ataque implementada (modificação e injeção de comandos, captura e *replay* em nível de byte). Sustenta metodologicamente a abordagem *on-path* do trabalho.
- **Metadados VERIFICADOS:** autores Peter Maynard e Kieran McLaughlin (Queen's University Belfast); publicado no 2º ICS-CSR (2014, BCS). **NÃO confirmei com total certeza:** o terceiro autor (Berthold Haberler), a paginação exata (30–42) e o DOI `10.14236/ewic/ICSCSR2014.5` — conferir na página oficial antes de citar. Título exato confirmado.
- **Fonte consultada:** https://pure.qub.ac.uk/en/publications/towards-understanding-man-in-the-middle-attacks-on-iec-60870-5-10/ (repositório institucional QUB).

> Observação: os mesmos autores têm um artigo posterior "Towards Understanding Man-on-the-Side Attacks (MotS) in SCADA Networks" (arXiv:2004.14334, 2020) que também trata de IEC-104. Menos central que o de 2014, mas citável como complemento.

### 1.2 `radoglou2019attacking` — Attacking IEC-60870-5-104 SCADA Systems (PRIORIDADE ALTA)

```bibtex
@inproceedings{radoglou2019attacking,
    Author = {Radoglou-Grammatikis, Panagiotis and Sarigiannidis, Panagiotis and Giannoulakis, Ioannis and Kafetzakis, Emmanouil and Panaousis, Emmanouil},
    Title = {Attacking {IEC-60870-5-104} {SCADA} Systems},
    Booktitle = {2019 IEEE World Congress on Services (SERVICES)},
    Pages = {41--46},
    Publisher = {IEEE},
    Year = {2019},
    Doi = {10.1109/SERVICES.2019.00022}}
```

- **Onde encaixa:** Cap. Ataque e Cap. Fundamentação — modela quatro tipos de ataque contra o IEC-104 (incluindo comandos não autorizados e DoS) por meio de uma *Coloured Petri Net*. Ancoram a taxonomia de ataques do trabalho ao protocolo específico, complementando a taxonomia genérica de DNP3 de `east2009taxonomy`.
- **Metadados VERIFICADOS:** título, evento (IEEE World Congress on Services 2019), páginas 41–46, DOI `10.1109/SERVICES.2019.00022`. Ordem/nomes dos cinco autores confirmados no registro Zenodo do autor.
- **Fontes:** https://ithaca.ece.uowm.gr/el/?p=6153 ; https://zenodo.org/records/3834759.

### 1.3 `bsi2022opcua` — BSI OPC UA Security Analysis (PRIORIDADE ALTA)

```bibtex
@techreport{bsi2022opcua,
    Author = {{Bundesamt f{\"u}r Sicherheit in der Informationstechnik}},
    Title = {{OPC UA} Security Analysis},
    Institution = {Bundesamt f{\"u}r Sicherheit in der Informationstechnik (BSI)},
    Address = {Bonn},
    Year = {2022},
    Url = {https://www.bsi.bund.de/SharedDocs/Downloads/EN/BSI/Publications/Studies/OPCUA/OPCUA_2022_EN.pdf},
    Urlaccessdate = {29 set. 2026}}
```

- **Onde encaixa:** Cap. Fundamentação, seção OPC-UA — análise oficial de órgão governamental (BSI alemão) sobre os mecanismos de segurança do OPC-UA (SecurityPolicies, modos de segurança, certificados X.509) e sua robustez. Reforça, com fonte primária independente, a discussão de segurança que hoje se apoia sobretudo em `dahlmanns2020opcua` e na norma IEC 62541.
- **Metadados VERIFICADOS:** publicação do BSI, versão 2022 (atualização da análise original de 2016/2017), disponível em inglês no domínio oficial `bsi.bund.de`. **NÃO confirmei:** o título exato impresso na capa (varia entre "OPC UA Security Analysis" e "Open Platform Communications Unified Architecture — Security Analysis") e a paginação — conferir no PDF.
- **Fonte:** https://www.bsi.bund.de/SharedDocs/Downloads/EN/BSI/Publications/Studies/OPCUA/OPCUA_2022_EN.pdf.

---

## 2. Normas e frameworks de segurança

### 2.1 `iec62443_3_3` — IEC 62443-3-3 (PRIORIDADE ALTA)

```bibtex
@manual{iec62443_3_3,
    Author = {{International Electrotechnical Commission}},
    Organization = {IEC},
    Title = {{IEC 62443-3-3}: Industrial communication networks -- Network and system security -- Part 3-3: System security requirements and security levels},
    Edition = {1.0},
    Address = {Geneva},
    Year = {2013}}
```

- **Onde encaixa:** Cap. Fundamentação, seção de segurança OT — a família ISA/IEC 62443 é o framework de referência para segurança de IACS e hoje está **ausente** do trabalho. A Parte 3-3 (7 requisitos fundamentais: IAC, UC, SI, DC, RDF, TRE, RA; e níveis de segurança SL 1–4) permite enquadrar os ataques demonstrados nos requisitos que eles violam — argumento forte perante a banca.
- **Metadados VERIFICADOS:** título e escopo (requisitos de sistema e níveis de segurança) confirmados; edição 1.0 de 2013 (IEC 62443-3-3:2013), ainda a edição vigente da Parte 3-3.
- **Fonte:** ficha oficial IEC (via https://www.evs.ee/en/evs-en-iec-62443-3-3-2019, que adota a IEC de 2013). Conferir a ficha diretamente no IEC Webstore antes de citar.

### 2.2 `iec101` — IEC 60870-5-101 (PRIORIDADE MÉDIA)

```bibtex
@manual{iec101,
    Author = {{International Electrotechnical Commission}},
    Organization = {IEC},
    Title = {{IEC 60870-5-101}: Telecontrol equipment and systems -- Part 5-101: Transmission protocols -- Companion standard for basic telecontrol tasks},
    Address = {Geneva},
    Year = {2003}}
```

- **Onde encaixa:** Cap. Fundamentação, seção IEC104 — o 104 é, por definição, o "acesso em rede para a IEC 60870-5-101 usando perfis de transporte padrão" (é o que o próprio título do 104 diz). Citar a 101 esclarece que as ASDUs, os tipos de informação e as causas de transmissão vêm do padrão-base, e que o 104 acrescenta o APCI/APDU sobre TCP.
- **Metadados A CONFIRMAR:** a IEC 60870-5-101 teve 1ª ed. (1995) + emendas e uma edição consolidada 2.1 (2003, base 2003 + Amd 1:2015 em revisões posteriores). **Confirmar edição/ano no IEC Webstore** (publicação IEC 60870-5-101) antes de fixar o campo `Year`.
- **Fonte a consultar:** IEC Webstore (buscar "60870-5-101").

### 2.3 `iec62264` — IEC 62264-1 / ISA-95 (PRIORIDADE BAIXA)

```bibtex
@manual{iec62264,
    Author = {{International Electrotechnical Commission}},
    Organization = {IEC},
    Title = {{IEC 62264-1}: Enterprise-control system integration -- Part 1: Models and terminology},
    Address = {Geneva},
    Year = {2013}}
```

- **Onde encaixa:** Cap. Fundamentação, se houver menção ao modelo Purdue / níveis de automação. **Avaliação:** valor **baixo** para este trabalho — a NIST SP 800-82r3 (já citada) apresenta a arquitetura de referência Purdue de forma citável, tornando a IEC 62264 largamente redundante. Incluir só se o texto discutir formalmente ISA-95/Purdue. Metadados (edição/ano) **a confirmar** no IEC Webstore.

---

## 3. Casos reais adicionais

### 3.1 `cherepanov2017industroyer` — Industroyer / CrashOverride (PRIORIDADE ALTA)

```bibtex
@techreport{cherepanov2017industroyer,
    Author = {Cherepanov, Anton},
    Title = {{Win32/Industroyer}: A New Threat for Industrial Control Systems},
    Institution = {ESET},
    Year = {2017},
    Url = {https://www.welivesecurity.com/wp-content/uploads/2017/06/Win32_Industroyer.pdf},
    Urlaccessdate = {29 set. 2026}}
```

- **Onde encaixa:** Cap. Introdução (motivação) e Fundamentação — Industroyer (2016, Kiev) foi o primeiro malware a manipular diretamente protocolos de telecontrole, **entre eles o IEC 60870-5-104**. É o caso real mais próximo do escopo do trabalho e complementa o dossiê Stuxnet (`falliere2011stuxnet`) e o ataque à Ucrânia de 2015 (`lee2016ukraine`).
- **Metadados VERIFICADOS:** autor Anton Cherepanov (ESET); apresentado no Black Hat USA 2017; *whitepaper* publicado pela ESET em jun. 2017. **NÃO confirmei** o caminho exato do PDF (a URL indicada é o padrão do WeLiveSecurity/ESET) — validar o link. A Dragos publicou análise paralela sob o nome "CRASHOVERRIDE" (Joe Slowik), citável em conjunto se desejado.
- **Fonte:** https://www.welivesecurity.com/2017/06/12/industroyer-biggest-threat-industrial-control-systems-since-stuxnet/.

### 3.2 `eset2022industroyer2` — Industroyer2 (PRIORIDADE ALTA)

```bibtex
@misc{eset2022industroyer2,
    Author = {{ESET Research}},
    Title = {{Industroyer2}: {Industroyer} Reloaded},
    Howpublished = {WeLiveSecurity (ESET)},
    Year = {2022},
    Url = {https://www.welivesecurity.com/2022/04/12/industroyer2-industroyer-reloaded/},
    Urlaccessdate = {29 set. 2026}}
```

- **Onde encaixa:** Cap. Introdução / Cap. Ataque — Industroyer2 (abr. 2022, Ucrânia) foi **construído especificamente para IEC-104**, com a configuração dos alvos (endereços de estações e IOAs) embutida no binário. É a demonstração mais atual da relevância prática de ataques sobre o exato protocolo central do trabalho.
- **Metadados VERIFICADOS:** publicação da ESET Research no WeLiveSecurity em 12 abr. 2022; análise em conjunto com o CERT-UA; foco em IEC-104; atribuição ao grupo Sandworm. Autoria coletiva (ESET Research) — se preferir autor nominal, os créditos do artigo listam pesquisadores da ESET (**confirmar nomes no artigo**).
- **Fonte:** https://www.welivesecurity.com/2022/04/12/industroyer2-industroyer-reloaded/.

### 3.3 `fireeye2017triton` — TRITON / TRISIS (PRIORIDADE MÉDIA)

```bibtex
@techreport{fireeye2017triton,
    Author = {{Mandiant (FireEye)}},
    Title = {Attackers Deploy New {ICS} Attack Framework {TRITON} and Cause Operational Disruption to Critical Infrastructure},
    Institution = {FireEye},
    Year = {2017},
    Url = {https://www.mandiant.com/resources/blog/attackers-deploy-new-ics-attack-framework-triton},
    Urlaccessdate = {29 set. 2026}}
```

- **Onde encaixa:** Cap. Introdução / Fundamentação — TRITON/TRISIS (2017) foi o primeiro malware a atacar um Sistema Instrumentado de Segurança (SIS Triconex, protocolo proprietário TriStation). Amplia o repertório de casos reais para além do setor elétrico, ilustrando a criticidade de segurança em OT. **Valor médio** porque o protocolo-alvo (TriStation) não está no escopo do gerador.
- **Metadados VERIFICADOS:** publicado pela Mandiant/FireEye em dez. 2017; alvo Triconex SIS (Schneider Electric); protocolo TriStation. **NÃO confirmei** o título exato nem a autoria nominal (o post original é atribuído a Blake Johnson e outros da FireEye) nem a URL atual (a Mandiant foi migrada para o domínio Google Cloud) — validar o link e o título.
- **Fonte:** https://cloud.google.com/blog/topics/threat-intelligence/ (buscar "TRITON").

---

## 4. Testbeds e datasets

### 4.1 `radoglou2022dataset` — IEC 60870-5-104 Intrusion Detection Dataset (PRIORIDADE MÉDIA)

```bibtex
@misc{radoglou2022dataset,
    Author = {Radoglou-Grammatikis, Panagiotis and Rompolos, Konstantinos and Lagkas, Thomas and Argyriou, Vasileios and Sarigiannidis, Panagiotis},
    Title = {{IEC 60870-5-104} Intrusion Detection Dataset},
    Howpublished = {Zenodo},
    Year = {2022},
    Doi = {10.21227/fj7s-f281},
    Url = {https://zenodo.org/record/7108614},
    Urlaccessdate = {29 set. 2026}}
```

- **Onde encaixa:** Cap. Metodologia / Resultados — dataset público, rotulado, de tráfego IEC-104 com doze ataques (comandos não autorizados e DoS). Serve como *baseline* comparável ao tráfego gerado pela ferramenta e como fonte de validação para eventual análise de detecção.
- **Metadados VERIFICADOS (no próprio registro Zenodo):** cinco autores (Radoglou-Grammatikis, Rompolos, Lagkas, Argyriou, Sarigiannidis), ano 2022, DOI `10.21227/fj7s-f281`, tráfego coletado entre abr. e jun. 2020, licença CC BY 4.0.
- **Fonte:** https://zenodo.org/record/7108614 (e espelho em IEEE DataPort).

### 4.2 `mathur2016swat` — SWaT testbed (PRIORIDADE MÉDIA)

```bibtex
@inproceedings{mathur2016swat,
    Author = {Mathur, Aditya P. and Tippenhauer, Nils Ole},
    Title = {{SWaT}: A Water Treatment Testbed for Research and Training on {ICS} Security},
    Booktitle = {2016 International Workshop on Cyber-physical Systems for Smart Water Networks (CySWater)},
    Pages = {31--36},
    Publisher = {IEEE},
    Year = {2016},
    Doi = {10.1109/CySWater.2016.7469060}}
```

- **Onde encaixa:** Cap. Fundamentação, seção de testbeds/simuladores — testbed físico de referência (iTrust/SUTD) muito citado, amplamente usado para gerar datasets de ataque em ICS. Complementa os testbeds já citados (SCADASim, MiniCPS, ICSSIM) posicionando a diferença entre testbed físico e simulação/geração de tráfego.
- **Metadados VERIFICADOS:** autores Aditya P. Mathur e Nils Ole Tippenhauer; evento CySWater 2016; páginas 31–36; DOI `10.1109/CySWater.2016.7469060`.
- **Fonte:** https://doi.org/10.1109/CySWater.2016.7469060 ; https://cispa.de/en/research/publications/67426-swat-a-water-treatment-testbed-for-research-and-training-on-ics-security.

---

## 5. Ferramentas / honeypots

### 5.1 `jicha2016conpot` — Análise do Conpot (PRIORIDADE MÉDIA)

```bibtex
@inproceedings{jicha2016conpot,
    Author = {Jicha, Arthur and Patton, Mark and Chen, Hsinchun},
    Title = {{SCADA} Honeypots: An In-depth Analysis of {Conpot}},
    Booktitle = {2016 IEEE Conference on Intelligence and Security Informatics (ISI)},
    Pages = {196--198},
    Publisher = {IEEE},
    Year = {2016},
    Doi = {10.1109/ISI.2016.7745468}}
```

- **Onde encaixa:** Cap. Fundamentação / Trabalhos relacionados — o Conpot é o honeypot ICS de referência (emula Modbus, IEC-104, entre outros). Fonte primária citável para contrastar a abordagem do trabalho (geração ativa de tráfego + MITM) com a de honeypots (coleta passiva de tráfego malicioso). Útil se o PFC discutir honeypots como alternativa ou complemento.
- **Metadados VERIFICADOS:** autores Arthur Jicha, Mark Patton, Hsinchun Chen (Univ. of Arizona); ISI 2016 (Tucson, 28–30 set. 2016); páginas 196–198. **NÃO confirmei o DOI** `10.1109/ISI.2016.7745468` diretamente na fonte primária — validar no IEEE Xplore/Crossref antes de citar (as demais informações estão confirmadas no repositório institucional).
- **Fonte:** https://experts.arizona.edu/en/publications/scada-honeypots-an-in-depth-analysis-of-conpot/.

> Nota sobre a ferramenta Conpot em si: pode ser citada como `@manual` apontando para https://github.com/mushorg/conpot, no mesmo estilo de `scadalts`/`wireshark`, se o trabalho a mencionar como software.

---

## 6. Não recomendadas / descartadas

| Candidata | Motivo do descarte |
|-----------|--------------------|
| **DNP3 Secure Authentication (SAv5)** como referência autônoma | O SAv5 já é normatizado **dentro** da IEEE 1815-2012 (já citada em `ieee1815`); não há norma separada de acesso aberto que agregue. Basta, no texto, atribuir o SA à própria IEEE 1815. |
| **IEC 62264-1 / ISA-95** (`iec62264`, listada como Baixa) | Amplamente redundante: a NIST SP 800-82r3 (já citada) apresenta o modelo Purdue de forma citável. Incluir só se houver discussão formal de ISA-95/Purdue. |
| **Modbus over Serial Line V1.02** e **Messaging on TCP/IP Implementation Guide V1.0b** | Úteis apenas para rigor pontual (nomenclatura mestre/escravo; expansão de "MBAP header"); já tratados em `analise_novas_referencias.md`. Fora do foco de "novas referências de alto valor". |
| **Blogs/notícias secundárias** (TheHackerNews, Dark Reading, Automation World sobre TRITON/Industroyer) | Fontes secundárias; preferíveis os relatórios primários dos fabricantes (ESET, Mandiant) já listados. |
| **Ferretti, Pogliani & Zanero — "Characterizing Background Noise..."** (honeypots ICS) | Bom paper, mas tangencial ao escopo (foco em ruído de fundo na Internet); o Conpot já cobre a lacuna de honeypots com menos dispersão. |
| **East et al. 2009 / Pliatsios 2020 / Dahlmanns 2020 / testbeds SCADASim, MiniCPS, ICSSIM** | Já presentes na bibliografia — não repetir. |

---

## Metodologia e URLs consultadas

- **Papers de ataque:** repositório QUB (Maynard) ; ithaca.ece.uowm.gr e Zenodo (Radoglou-Grammatikis 2019 e dataset 2022) ; DOI/CISPA (Mathur & Tippenhauer).
- **Casos reais:** WeLiveSecurity/ESET (Industroyer 2017 e Industroyer2 2022) ; Mandiant/Google Cloud (TRITON).
- **Normas:** fichas IEC (62443-3-3, 60870-5-101, 62264-1) — recomenda-se confirmar edição/ano diretamente no IEC Webstore.
- **Órgão oficial:** BSI (bsi.bund.de) — OPC UA Security Analysis 2022.
- **Honeypot:** experts.arizona.edu (Jicha, Patton & Chen 2016).

### Itens NÃO confirmados (revisar antes de inserir em `refs.bib`)
- `maynard2014mitm`: terceiro autor, paginação e DOI.
- `bsi2022opcua`: título exato na capa e paginação.
- `iec101` e `iec62264`: edição e ano (conferir no IEC Webstore).
- `cherepanov2017industroyer`: caminho exato do PDF.
- `eset2022industroyer2`: autoria nominal (se não usar "ESET Research").
- `fireeye2017triton`: título exato, autoria nominal e URL atual (migração Mandiant → Google Cloud).
- `jicha2016conpot`: DOI (`10.1109/ISI.2016.7745468`).
