# Eval cases for review

42 questions written before any tuning. Answerable and change cases pass when every fact group appears in the answer (any listed alternative counts); a citation counts when it comes from an allowed document whose page contains an expected fact. Unanswerable cases pass when the app says "not in the documents" or withholds.

## Answerable (26)

| id | question | must contain | citation from | trap |
|---|---|---|---|---|
| a01-capacity-permit | What generating capacity does the TCEQ permit file give for the Kilby plant? | 2,869 | tceq-application-2026-tc | Earlier 2,595 and 2,476 MW figures are on other pages of the same file. |
| a02-capacity-original | What capacity did the original October 2025 permit application state? | 2,595 | tceq-application |  |
| a03-capacity-chevron | What capacity did Chevron announce for Project Kilby? | 2.67 | chevron-release, techcrunch, eenews, powereng, latitude, energycapital, joulent-release |  |
| a04-turbine-count | How many turbines are in the updated permit application? | 46 | tceq-application-2026-tc |  |
| a05-t130-purpose | Why were the Solar Titan 130 turbines added? | 29 and early phase / early-phase | tceq-application-2026-tc |  |
| a06-7ha-rating | What is the net output of each GE 7HA.02 combined-cycle turbine? | 555.7 | tceq-application | The 600 MW figure in an older summary on the same file. |
| a07-ppa-term | How long is the power purchase agreement? | 20 | chevron-release, techcrunch, eenews, powereng, latitude, energycapital, joulent-release |  |
| a08-offtaker | Who will buy the plant's power? | Microsoft | chevron-release, techcrunch, eenews, powereng, latitude, energycapital, joulent-release, semafor |  |
| a09-applicant | Which company applied for the air permit? | Energy Forge One | tceq- |  |
| a10-county | Which Texas county is the plant in? | Reeves | tceq-, chevron-release, ercot-gis, semafor |  |
| a11-substation | Which substation does the project connect to in the ERCOT queue? | Solstice | ercot-gis | The unrelated Kilby Station / Kilby Switching Station in Haskell County. |
| a12-battery-now | How large is the battery storage portion in the latest ERCOT report? | 330 | ercot-gis-2026-10, ercot-gis-2026-09, ercot-gis-2026-08, ercot-gis-2026-07 | 560 MW in earlier reports; Quantum Storage at 321.75 MW is a different project. |
| a13-part1-cod | What is the projected commercial operation date for Kilby1 Part 1? | 2027-11-04 / November 4, 2027 | ercot-gis |  |
| a14-first-power | When does Chevron expect first power? | 2028 | chevron-release, techcrunch, eenews, powereng, latitude, energycapital, joulent-release, semafor |  |
| a15-fid-timing | When does Chevron expect to make its final investment decision? | end of 2026 / 2026 | chevron-release, semafor, techcrunch, eenews, powereng |  |
| a16-co2e-updated | What annual CO2e emissions does TCEQ list for the updated 46 turbine project? | 13,890,275 | tceq-application-2026-tc | 11,564,704.59 in the plain language summary is the original project. |
| a17-co2e-pls | What CO2 equivalent total does the plain language summary list? | 11,564,704 | tceq-pls, tceq-application |  |
| a18-nox-controls | How will NOx emissions be controlled? | selective catalytic reduction / SCR | tceq-, chevron-release |  |
| a19-water | What water source will the plant use? | brackish | chevron-release |  |
| a20-filed-date | When was the air permit application submitted to TCEQ? | October 17, 2025 / 10/17/2025 / 2025-10-17 | tceq- |  |
| a21-public-meeting | When and where was the TCEQ public meeting on the permit held? | June 10, 2026 and Pecos | tceq-meeting |  |
| a22-permit-number | What is the state air quality permit number? | 181895 | tceq- |  |
| a23-suppliers | Who supplies the turbines? | GE Vernova / General Electric / GE 7HA and Solar Turbines / Solar Titan / Caterpillar | chevron-release, tceq-application, techcrunch, eenews |  |
| a24-national-grid | How much did National Grid invest in Joulent, and for what stake? | 1.75 and 35 | joulent-release, powereng, latitude |  |
| a25-final-issuance-estimate | When did TCEQ estimate the permit would be issued? | 10/15/2026 / October 15, 2026 | tceq-application-2026-tc |  |
| a26-technically-complete | On what date did TCEQ estimate the application would be technically complete? | 4/15/2026 / April 15, 2026 | tceq-application-2026-tc |  |

## Change over time (6)

| id | question | must contain | citation from | trap |
|---|---|---|---|---|
| c01-battery-change | How has the size of the battery storage portion changed in the ERCOT queue? | 560 and 330 | ercot-gis |  |
| c02-capacity-change | How did the permit application's capacity change between the original filing and the update? | 2,595 and 2,869 | tceq-application |  |
| c03-turbine-change | How did the number of turbines change between the original application and the update? | 17 and 46 | tceq-application |  |
| c04-permit-status | How has the air permit application's status on TCEQ's pending list changed? | Administrative and Technically | tceq-pending, tceq-application-2026-tc |  |
| c05-solar-fate | What happened to the solar part of the Kilby queue entries? | cancel | ercot-gis |  |
| c06-part6-technology | Did the technology listed for Kilby1 Part 6 change in the ERCOT reports? | CC / combined and GT / gas turbine / simple | ercot-gis |  |

## Not in the documents (10)

| id | question | must contain | citation from | trap |
|---|---|---|---|---|
| u01-turbine-cost | How much did the GE Vernova turbines cost? | says not in the documents |  |  |
| u02-epc-contractor | Which company is the EPC contractor for the plant? | says not in the documents |  | The permit file mentions "the EPC agreement" and carbon capture EPC costs, but never names a contractor. |
| u03-construction-cost | What is the total construction cost of the Kilby power plant? | says not in the documents |  | $10 billion in tax revenue and $407 million carbon capture EPC costs are on nearby pages. |
| u04-ppa-price | What price per MWh will Microsoft pay under the PPA? | says not in the documents |  | The permit file has $/MWh figures for carbon capture economics. |
| u05-acreage | How many acres is the Kilby site? | says not in the documents |  |  |
| u06-lcoe | What is the plant's levelized cost of electricity? | says not in the documents |  |  |
| u07-mercuria | How many megawatts is the Pecos Power Plant that Mercuria is building with a Texas Energy Fund loan? | says not in the documents |  | Same county, different project. |
| u08-engine-no1-price | How much did Chevron pay Engine No. 1 for its share of Project Kilby? | says not in the documents |  | National Grid's $1.75 billion for 35% of Joulent is a different transaction. |
| u09-dc-contractor | Who is the general contractor building Microsoft's data center in Pecos? | says not in the documents |  |  |
| u10-revenue | How much annual revenue will the plant earn from Microsoft? | says not in the documents |  | Chevron's release mentions $10 billion in state and local tax revenue and mid-teen returns. |
