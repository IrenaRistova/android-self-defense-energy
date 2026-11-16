# Sources of Table OA5

Written by `make_online_appendix.py`; do not edit by hand. Table OA5 of the online appendix (`tables/table_tool_families.tex`) cites nothing itself: this file gives the source of each of its entries, by the table's rows. `references.bib` holds the same 17 works in BibTeX. The vendor pages were read on 16 November 2026.

## Panel (a): the three protection families and their candidate tools

The papers below back each family. Per-tool sources and reasons for exclusion: `6_results_analysis/tables/candidate_tools.csv`.

### Obfuscation (code transformation)

Candidate tools: ProGuard, R8, DexGuard, Allatori (measured), DashO, Obfuscapk.

- Parvez Faruki, Hossein Fereidooni, Vijay Laxmi, Mauro Conti and Manoj Gaur (2016). Android Code Protection via Obfuscation Techniques: Past, Present and Future Directions. arXiv preprint arXiv:1611.10231. https://arxiv.org/abs/1611.10231 `[Faruki2017AndroidCodeProtectionSurvey]`

### Packing (loader-based)

Candidate tools: Bangcle (measured), Qihoo 360 Jiagu, Tencent Legu, Ijiami, AliProtect, Baidu, Naga, Manxi, APKProtect, SecNeo.

- Yue Duan, Mu Zhang, Abhishek Vasisht Bhaskar, Heng Yin, Xiaorui Pan, Tongxin Li, Xueqiang Wang and XiaoFeng Wang (2018). Things You May Not Know About Android (Un)Packers: A Systematic Study Based on Whole-System Emulation. Proceedings of the 2018 Network and Distributed System Security Symposium (NDSS 2018). https://doi.org/10.14722/ndss.2018.23296 `[Duan2018AndroidUnpackers]`
- Zikan Dong, Hongxuan Liu, Liu Wang, Xiapu Luo, Yao Guo, Guoai Xu, Xusheng Xiao and Haoyu Wang (2022). What Did You Pack in My App? A Systematic Analysis of Commercial Android Packers. Proceedings of the 30th ACM Joint European Software Engineering Conference and Symposium on the Foundations of Software Engineering (ESEC/FSE 2022), pp. 1430-1440. https://doi.org/10.1145/3540250.3558969 `[Dong2022PackDiff]`
- Eduardo Blázquez and Juan Tapiador (2025). Practical Android Software Protection in the Wild. ACM Computing Surveys, vol. 58, no. 2, pp. 36:1-36:32. https://doi.org/10.1145/3757735 `[BlazquezTapiador2025AndroidProtectionWild]`

### Runtime shielding (RASP)

Candidate tools: freeRASP (measured), DexProtector, Appdome, Promon SHIELD, LIAPP, WhiteCryption, V-Key, Virbox Protector, AppSealing.

- Parvez Faruki, Hossein Fereidooni, Vijay Laxmi, Mauro Conti and Manoj Gaur (2016). Android Code Protection via Obfuscation Techniques: Past, Present and Future Directions. arXiv preprint arXiv:1611.10231. https://arxiv.org/abs/1611.10231 `[Faruki2017AndroidCodeProtectionSurvey]`
- Eduardo Blázquez and Juan Tapiador (2025). Practical Android Software Protection in the Wild. ACM Computing Surveys, vol. 58, no. 2, pp. 36:1-36:32. https://doi.org/10.1145/3757735 `[BlazquezTapiador2025AndroidProtectionWild]`

## Panel (b): the three tools measured

Each entry as the table gives it, the evidence for it, and the page it comes from.

### Allatori

- **Vendor (country)**: Smardec Inc. (country not stated).
  - Evidence: The footer of the vendor's pages reads "Copyright (c) 2005-2026 Smardec Inc.". None of the vendor's pages (allatori.com and smardec.com) nor the licence that comes with the tool states an address, a city or a country.
  - Source: Allatori Java Obfuscator: Overview, https://allatori.com/overview.html, read on 16 November 2026. `[oa_allatori_overview]`
- **Version**: 9.8, educational edition (released 1 June 2026).
  - Evidence: The changelog lists "Version 9.8 - June 01, 2026". The measured builds use the educational edition of 9.8; 9.9 came out on 1 September 2026.
  - Source: Allatori Java Obfuscator: Changelog, https://allatori.com/changelog.html, read on 16 November 2026. `[oa_allatori_changelog]`
- **Licence and cost**: Educational licence (used here): free on request. Commercial: US$290 per developer, a licence with no time limit (12 months of updates included).
  - Evidence: The price page: "Free for educational and non-commercial projects"; such developers "may be eligible to receive Allatori Obfuscator for free" by contacting the vendor. Single Developer Licence US$290, with 12 months of email support, updates and upgrades. The licence in the tool's distribution: "There is no time limitation on using the Software Product".
  - Source: Allatori Java Obfuscator: Price list, https://allatori.com/price.html, read on 16 November 2026. `[oa_allatori_price]`
- **Key characteristic**: Renames classes, methods and fields, encrypts strings (an added method decrypts them at run time) and obfuscates control flow, in the compiled classes at build time.
  - Evidence: The documentation: string encryption "enable (default) ... Allatori will add a method to decrypt strings on run-time"; control-flow obfuscation "enable (default)".
  - Source: Allatori Java Obfuscator: Documentation, https://allatori.com/doc.html, read on 16 November 2026. `[oa_allatori_doc]`

### Bangcle

- **Vendor (country)**: Beijing Bangcle Security Technology Co., Ltd. (China).
  - Evidence: Clause 1.1 of the user service agreement names the company 北京梆梆安全科技有限公司 (北京: Beijing) and the mark "Bangcle". The English company name is a translation.
  - Source: Bangcle user service agreement, https://passport.bangcle.com/authcenter/Users/agreement (in Chinese), read on 16 November 2026. `[oa_bangcle_agreement]`
- **Version**: None reported by the service (builds packed in May and June 2026).
  - Evidence: No version is published. The measured version is the service as it ran when the builds were packed (12 May, 15 May and 29 June 2026).
  - Source: the packed builds themselves; no page of the service gives a version.
- **Licence and cost**: Standard tier (used here): free. Customised hardening: paid, price on request.
  - Evidence: The help centre: "标准版应用加固服务是免费的，定制版加固服务为收费版本，可以在线联系客服人员咨询详情" (the standard hardening service is free; the customised one is paid; ask customer service for details).
  - Source: Help centre: questions on application hardening, https://dev.bangcle.com/devwebsite/home/help?id=11 (in Chinese), read on 16 November 2026. `[oa_bangcle_faq]`
- **Key characteristic**: Encrypts the bytecode into a payload that a stub decrypts and loads at run time, applied to the finished APK.
  - Evidence: The feature page: the service extracts all code from classes.dex and encrypts and packs the whole file ("抽取classes.dex中所有代码 ... 整体文件深度加密加壳"). The packed APKs carry the payload assets/classes0.jar, a stub classes.dex (com.SecShell) and libSecShell.so.
  - Source: Features: application hardening, https://dev.bangcle.com/devwebsite/home/introduction?type=0 (in Chinese), read on 16 November 2026. `[oa_bangcle_features]`

### freeRASP

- **Vendor (country)**: Talsec, Lynx SFT s.r.o. (Czech Republic).
  - Evidence: The contact page: "Lynx SFT, s.r.o Main office: Lidická 2030/20 Brno, 602 00 Czech Republic".
  - Source: Contact us, https://www.talsec.app/contact, read on 16 November 2026. `[oa_talsec_contact]`
- **Version**: Community 18.0.2 (released 10 February 2026).
  - Evidence: Release 18.0.2 of the Android SDK was published on 10 February 2026. The measured builds use TalsecSecurity-Community 18.0.2 (tools/pack_freerasp.sh).
  - Source: freeRASP for Android: Release 18.0.2, https://github.com/talsec/Free-RASP-Android/releases/tag/18.0.2 (published 10 February 2026), read on 16 November 2026. `[oa_freerasp_release]`
- **Licence and cost**: Community tier (used here): free up to 100,000 devices, with mandatory data collection by the vendor. Paid plans from EUR 284 per month.
  - Evidence: The Fair Usage Policy (version 1.1, effective 13 October 2025): freeRASP is free while the customer's applications together stay under 100,000 unique devices, and Talsec's collection of threat and telemetry data is mandatory. The plans on the vendor's home page start at EUR 284 per month (RASP+ Starter).
  - Source: freeRASP: Fair Usage Policy, version 1.1, https://docs.talsec.app/freerasp/terms-of-service/fair-usage-policy-fup (effective 13 October 2025), read on 16 November 2026. `[oa_freerasp_fup]`
  - Source: Talsec: Plans and prices, https://www.talsec.app/ (section "Select the right option for you"), read on 16 November 2026. `[oa_talsec_plans]`
- **Key characteristic**: A library in the application's process that checks for root, debuggers, hooking frameworks, emulators and tampering, at launch and periodically.
  - Evidence: The features page lists the checks. The FAQ: "One-time checks are executed only once when the app is launched ... Periodic checks are performed regularly, at defined intervals". Here its threat callbacks only log each threat and never stop the application (the listener tools/pack_freerasp.sh adds).
  - Source: freeRASP: Features and pricing plans, https://docs.talsec.app/freerasp/freerasp/features-and-pricing-plans, read on 16 November 2026. `[oa_freerasp_features]`
  - Source: freeRASP: FAQ, https://docs.talsec.app/freerasp/freerasp/faq, read on 16 November 2026. `[oa_freerasp_faq]`
