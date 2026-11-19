# Project Kilby corpus

Gathered 2026-11-19. Public sources only. Project: Kilby Power Plant, Energy Forge One LLC (Chevron, with Engine No. 1 / Joulent), near Pecos, Reeves County, TX.

Filename dates are publication dates. Two exceptions: the TCEQ plain language summary is filed under the application date (10/17/2025) because the summary itself carries no date, and the NAPD date (4/15/2026) comes from secondary coverage.

## Tiers (the answer layer should prefer higher tiers and say when it falls back)

1. **Primary regulatory:** `tceq/` (4 notices + meeting page), `tceq/application/` (the full permit application, see below), `ercot/` (14 monthly GIS reports, Sep 2025 to Oct 2026).
2. **Company statements:** `wayback/2026-06-22_chevron-newsroom_microsoft-ppa` and `wayback/2026-06-22_businesswire_chevron-microsoft-ppa` (Chevron's 6/22/2026 release, originals via the Wayback Machine); `wayback/2026-07-19_joulent_in-the-news_strategic-investment` (Joulent's National Grid release); `wayback/2026-08-10_joulent_about-us` and `_power-stack` (team bios, Across the Meter description). `press/2026_energy-pedia_chevron-microsoft` is a syndicated copy, kept as a cross-check.

## The TCEQ permit application (`tceq/application/`)

TCEQ posts each review milestone as a PDF portfolio (`399548-ac.pdf`, `399548-tc.pdf` under `/assets/public/permitting/air/reports/applications/`). The GEM wiki snapshot cited the link. The embedded files were pulled out with `pdfdetach -saveall`.

- `2025-10-17_admin-complete/`: the original application, 257 pages, plus emissions workbooks.
- `2026-08_technically-complete/`: 1,100 pages covering the application, correspondence with TCEQ, the draft permit (with special conditions), the preliminary determination summary and the technical review, plus the 12/23/2025 updated workbook, Feb 2026 revisions and a Mar 2026 MERA analysis.

Facts now verified in the primary record:
- **Original (Oct 2025):** 17 turbines, "approximately 2,595 megawatts". These are 2 GE 7HA.02 combined cycle units, 3 GE 7HA.02 simple cycle units and 12 Solar Titan 350 units.
- **Updated (12/22/2025):** 46 turbines, "approximately 2,869 megawatts (MW) at site conditions". The update added 29 Solar Titan 130 units (13.55 MW each) for "early phase power", which are "not intended to be onsite long-term". Unit ratings: 7HA.02 combined cycle 555.7 MW, 7HA.02 simple cycle 330.3 MW, T350 31.1 MW.
- **Restated at site conditions:** asked by TCEQ to define "site conditions", EFO answered that the original 17 turbines give "a total nominal net output of 2,476 megawatts (MW)" (`tceq-application-2026-tc` p. 681). The unit ratings check out: 2 x 555.7 + 3 x 330.3 + 12 x 31.1 = 2,475.5 MW, and adding 29 x 13.55 gives 2,868.5, the 2,869 MW in the update. So 2,595 was the first nominal figure, not a different design.
- **Internal inconsistencies worth eval questions:** one summary still says "two 600-MW combined-cycle turbines ... total capacity of 2,595 MW" next to the 555.7 MW unit rating; an equipment ID range is mistyped as "T130SC1 – T350SC29"; the BACT tables quote other permits (LNG trains, GE LM2500, GE 7FA) that are not Kilby equipment. That last one is a retrieval trap: a naive answer will cite LNG facts as Kilby's.

## TCEQ status over time (`wayback/tceq-pending/`, six snapshots of TCEQ's pending permit list)

"Administrative Complete Application" in the Jan, Feb and Apr 2026 snapshots; "Technically Complete Application" from the Aug 6, 2026 snapshot onward. The posted notices themselves did not change: the Jan 2026 file sizes match today's downloads.
3. **Press:** TechCrunch, E&E News, Semafor, Latitude Media, Power Engineering, Energy Capital HTX.
4. **Third-party analysis:** Frontier Grid readiness brief (49 pages, 7/28/2026), Compute Atlas. Useful, but not authoritative.

## Kilby rows in the ERCOT queue (`ercot/kilby_rows_by_month.csv`)

All at Solstice Substation 345 kV, Reeves County.

| INR | Part | MW (latest) | Projected COD | First seen |
|---|---|---|---|---|
| 27INR0572 | Part 1 small block simple cycle | 371 | 2027-11-04 | Oct 2025 report |
| 28INR0459 | Part 4 large block simple cycle | 950 | 2028-04-20 | Nov 2025 report |
| 28INR0457 | Part 2 Tier 1 BESS | 330 | 2028-01-21 | Jan 2026 report |
| 29INR0199 | Part 6 large block combined cycle | 1,056 | 2029-07-19 | Feb 2026 report |
| 28INR0460 | Part 5 solar | cancelled | | Aug 2026 cancellation sheet |

Part 3 never appears in any report.

## What changed, from the primary record (ready made "what changed" demo content)

- **BESS cut from 560 MW to 330 MW** between the Jul 2026 and Aug 2026 reports (28INR0457).
- **Part 5 solar cancelled** in the Aug 2026 report (28INR0460).
- **Part 6 technology code changed from CC (combined cycle) to GT** between the Sep and Oct 2026 reports (29INR0199), while the name still says "Combined Cycle". A good case for a `conflict` flag inside a single document.
- **Study phases:** each part moved from "SS Started" to "SS Completed"; none has an interconnection agreement (all "No IA").
- **Projected CODs never moved** across the 14 reports.

## Conflicts across sources (record card `conflict` cases)

- **Capacity:** 2.67 GW (Chevron release and most press); "nearly 3 gigawatts" (Semafor); ERCOT gas rows total 2,377 MW (2,707 MW with the battery); 2,595 MW / 17 turbines in the Oct 2025 application and 2,869 MW / 46 turbines after the 12/22/2025 update (both verified in `tceq/application/`); GEM wiki (Apr 2026 snapshot) shows CC1 987 MW + GT1-15 1,608 MW = 2,595 MW, so GEM was still on the original application.
- **CO2:** 11,564,704.59 tons CO2e per year (TCEQ plain language summary) vs "more than 13 million tons" (EIP, as quoted by TechCrunch).

## Disambiguation traps (refusal / wrong project questions)

- **"Kilby Station 345kV" / "Kilby Switching Station"** in Haskell County appears in the same ERCOT reports for Quantum Solar and Quantum Storage. It is unrelated to Project Kilby.
- **Pecos Power Plant LLC** (Mercuria, Texas Energy Fund loan, also Reeves County) is a different project.

## Still missing

- **EIP's own analysis.** Not on the live site or in the Wayback Machine; only the TechCrunch quote is in hand.
- **TCEQ response to comments / final permit.** None found as of 11/19; the 1,100 page portfolio mentions "Response to Comments" only as a process step. TCEQ Records Online (down 11/19) may hold public comment letters.
- Found via the Wayback Machine on 11/19: the full application (through the GEM link), the GEM page (Apr 2026), Chevron's original release, Joulent's National Grid release.

Size: about 157 MB, mostly the two application PDFs. Use Git LFS or keep `tceq/application/` out of the public repo with a fetch script.
