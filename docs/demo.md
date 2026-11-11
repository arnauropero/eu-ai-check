# Demo: five recorded examples

Five new descriptions, run once on 2026-11-04 through the frozen pipeline (`gpt-6.1-sol` for every stage). The answers are shown **exactly as produced, unedited** (X04 is re-rendered by code; see its note). This is a demonstration, not an evaluation: the examples have no labels and are not scored. Titles and notes are our annotations.

Total demo spend: $0.3540. The same output in raw form is in [`data/demo/recording.md`](../data/demo/recording.md); the full record is [`recording.json`](../data/demo/recording.json). Known weaknesses visible here are listed in [limitations.md](limitations.md).

## X01: Language-school placement tool

> We run a chain of language schools. We built PlaceRight, a tool that scores each new student's online entry test and decides which course level they are placed in; staff do not review the placement. We use it in our own schools under our own name. Which AI Act rules apply to us on 1 November 2026?

| Determination | Answer |
|---|---|
| Risk tier (Article 6) | high-risk. |
| Prohibited practices (Article 5) | no prohibited practice was found for this case. |
| Role (deployer) | you are a deployer of this system. |
| Role (provider) | you are a provider of this system. |
| Article 16 | applies to this case. |
| Article 26 | applies to this case. |

Cost: $0.0877 (3 API calls). Template fallback: no.

**Note:** this answer follows the 2024 text the tool uses. Regulation (EU) 2026/1744, in force since 27 July 2026, moved the high-risk rules for Annex III systems, including Articles 16 and 26, to 2 December 2027, so under the amended Act they do not apply yet on 1 November 2026.

<details>
<summary>Full answer, unedited</summary>

```text
## Risk tier (Article 6): high-risk.
- The system’s evaluation of learning outcomes and assessment of the education level an individual will receive or access correspond to the educational uses listed in Annex III(3)(b) and (c). Article 6(2) links the risk classification to those listed uses. [Article 6(2); Annex III(3)(b); Annex III(3)(c)]
- Article 6(3), third subparagraph, makes significant harm, material influence over decisions and profiling relevant to the treatment of listed systems. Profiling prevents reliance on the paragraph’s reduced-risk exception. [Article 6(3), third subparagraph]
Sources: Article 6(2) (aiact:art_6:para_2@2024-08-01); Article 6(1)(a) (aiact:art_6:para_1:point_a@2024-08-01); Annex III(3)(b) (aiact:anx_III:area_3:item_b@2024-08-01); Annex III(3)(c) (aiact:anx_III:area_3:item_c@2024-08-01); Article 6(3), third subparagraph (aiact:art_6:para_3:subpara_3@2024-08-01)

## Prohibited practices (Article 5): no prohibited practice was found for this case.
- The described practice has no identified features corresponding to the practices listed in Article 5(1)(a)–(h). [Article 5(1)(a); Article 5(1)(b); Article 5(1)(c); Article 5(1)(d); Article 5(1)(e); Article 5(1)(f); Article 5(1)(g); Article 5(1)(h)]
Sources: Article 5(1)(a) (aiact:art_5:para_1:point_a@2024-08-01); Article 5(1)(b) (aiact:art_5:para_1:point_b@2024-08-01); Article 5(1)(c) (aiact:art_5:para_1:point_c@2024-08-01); Article 5(1)(d) (aiact:art_5:para_1:point_d@2024-08-01); Article 5(1)(e) (aiact:art_5:para_1:point_e@2024-08-01); Article 5(1)(f) (aiact:art_5:para_1:point_f@2024-08-01); Article 5(1)(g) (aiact:art_5:para_1:point_g@2024-08-01); Article 5(1)(h) (aiact:art_5:para_1:point_h@2024-08-01)

## Role (deployer): you are a deployer of this system.
Sources: Article 3(4) (aiact:art_3:point_4@2024-08-01)

## Role (provider): you are a provider of this system.
Sources: Article 3(3) (aiact:art_3:point_3@2024-08-01); Article 25(1)(a) (aiact:art_25:para_1:point_a@2024-08-01); Article 25(1)(b) (aiact:art_25:para_1:point_b@2024-08-01); Article 3(23) (aiact:art_3:point_23@2024-08-01); Article 25(1)(c) (aiact:art_25:para_1:point_c@2024-08-01)

## Article 16: applies to this case.
- For this system, Article 16 requires compliance with the system requirements, provider identification and contact details, a quality management system, retention of documentation and retention of automatically generated logs under the provider’s control. [Article 16(a); Article 16(b); Article 16(c); Article 16(d); Article 16(e)]
- Article 16 requires conformity assessment before market placement or putting into service, an EU declaration of conformity, CE marking and registration. [Article 16(f); Article 16(g); Article 16(h); Article 16(i)]
- Article 16 also requires corrective action and information provision, demonstration of conformity upon a reasoned request from a national competent authority, and compliance with accessibility requirements. [Article 16(j); Article 16(k); Article 16(l)]
- Article 16(a): Providers of high-risk AI systems shall: ensure that their high-risk AI systems are compliant with the requirements set out in Section 2;
- Article 16(b): Providers of high-risk AI systems shall: indicate on the high-risk AI system or, where that is not possible, on its packaging or its accompanying documentation, as applicable, their name, registered trade name or registered trade mark, the address at which they can be contacted;
- Article 16(c): Providers of high-risk AI systems shall: have a quality management system in place which complies with Article 17;
- Article 16(d): Providers of high-risk AI systems shall: keep the documentation referred to in Article 18;
- Article 16(e): Providers of high-risk AI systems shall: when under their control, keep the logs automatically generated by their high-risk AI systems as referred to in Article 19;
- Article 16(f): Providers of high-risk AI systems shall: ensure that the high-risk AI system undergoes the relevant conformity assessment procedure as referred to in Article 43, prior to its being placed on the market or put into service;
- Article 16(g): Providers of high-risk AI systems shall: draw up an EU declaration of conformity in accordance with Article 47;
- Article 16(h): Providers of high-risk AI systems shall: affix the CE marking to the high-risk AI system or, where that is not possible, on its packaging or its accompanying documentation, to indicate conformity with this Regulation, in accordance with Article 48;
- Article 16(i): Providers of high-risk AI systems shall: comply with the registration obligations referred to in Article 49(1);
- Article 16(j): Providers of high-risk AI systems shall: take the necessary corrective actions and provide information as required in Article 20;
- Article 16(k): Providers of high-risk AI systems shall: upon a reasoned request of a national competent authority, demonstrate the conformity of the high-risk AI system with the requirements set out in Section 2;
- Article 16(l): Providers of high-risk AI systems shall: ensure that the high-risk AI system complies with accessibility requirements in accordance with Directives (EU) 2016/2102 and (EU) 2019/882.
Sources: Article 16(a) (aiact:art_16:point_a@2024-08-01); Article 16(b) (aiact:art_16:point_b@2024-08-01); Article 16(c) (aiact:art_16:point_c@2024-08-01); Article 16(d) (aiact:art_16:point_d@2024-08-01); Article 16(e) (aiact:art_16:point_e@2024-08-01); Article 16(f) (aiact:art_16:point_f@2024-08-01); Article 16(g) (aiact:art_16:point_g@2024-08-01); Article 16(h) (aiact:art_16:point_h@2024-08-01); Article 16(i) (aiact:art_16:point_i@2024-08-01); Article 16(j) (aiact:art_16:point_j@2024-08-01); Article 16(k) (aiact:art_16:point_k@2024-08-01); Article 16(l) (aiact:art_16:point_l@2024-08-01)

## Article 26: applies to this case.
- For use of this system, Article 26 requires technical and organisational measures supporting use according to instructions, and human oversight by people with sufficient competence, training, authority and support. Where the user controls the data entering the system, that data must be relevant and sufficiently representative for its intended purpose. [Article 26(1); Article 26(2); Article 26(4)]
- Article 26 requires operational monitoring, notification and suspension when the specified risk conditions arise, and immediate notification of serious incidents. Automatically generated logs under the user’s control must be retained for an appropriate period of at least six months, unless Union or national law provides otherwise. [Article 26(5); Article 26(6)]
- Article 26 requires advance information for affected workers and their representatives when an employer introduces the system at work, and registration-related checks and actions for public-sector users. Where a data protection impact assessment is required, the user must use the information provided for that purpose. [Article 26(7); Article 26(8); Article 26(9)]
- When the system makes or assists decisions concerning individuals, Article 26(11) requires informing those individuals of its use. Article 26(12) also requires cooperation with competent authorities implementing the Regulation. [Article 26(11); Article 26(12)]
- Article 26(1): Deployers of high-risk AI systems shall take appropriate technical and organisational measures to ensure they use such systems in accordance with the instructions for use accompanying the systems, pursuant to paragraphs 3 and 6.
- Article 26(2): Deployers shall assign human oversight to natural persons who have the necessary competence, training and authority, as well as the necessary support.
- Article 26(3): The obligations set out in paragraphs 1 and 2, are without prejudice to other deployer obligations under Union or national law and to the deployer’s freedom to organise its own resources and activities for the purpose of implementing the human oversight measures indicated by the provider.
- Article 26(4): Without prejudice to paragraphs 1 and 2, to the extent the deployer exercises control over the input data, that deployer shall ensure that input data is relevant and sufficiently representative in view of the intended purpose of the high-risk AI system.
- Article 26(5): Deployers shall monitor the operation of the high-risk AI system on the basis of the instructions for use and, where relevant, inform providers in accordance with Article 72. Where deployers have reason to consider that the use of the high-risk AI system in accordance with the instructions may result in that AI system presenting a risk within the meaning of Article 79(1), they shall, without undue delay, inform the provider or distributor and the relevant market surveillance authority, and shall suspend the use of that system. Where deployers have identified a serious incident, they shall also immediately inform first the provider, and then the importer or distributor and the relevant market surveillance authorities of that incident. If the deployer is not able to reach the provider, Article 73 shall apply mutatis mutandis. This obligation shall not cover sensitive operational data of deployers of AI systems which are law enforcement authorities. For deployers that are financial institutions subject to requirements regarding their internal governance, arrangements or processes under Union financial services law, the monitoring obligation set out in the first subparagraph shall be deemed to be fulfilled by complying with the rules on internal governance arrangements, processes and mechanisms pursuant to the relevant financial service law.
- Article 26(6): Deployers of high-risk AI systems shall keep the logs automatically generated by that high-risk AI system to the extent such logs are under their control, for a period appropriate to the intended purpose of the high-risk AI system, of at least six months, unless provided otherwise in applicable Union or national law, in particular in Union law on the protection of personal data. Deployers that are financial institutions subject to requirements regarding their internal governance, arrangements or processes under Union financial services law shall maintain the logs as part of the documentation kept pursuant to the relevant Union financial service law.
- Article 26(7): Before putting into service or using a high-risk AI system at the workplace, deployers who are employers shall inform workers’ representatives and the affected workers that they will be subject to the use of the high-risk AI system. This information shall be provided, where applicable, in accordance with the rules and procedures laid down in Union and national law and practice on information of workers and their representatives.
- Article 26(8): Deployers of high-risk AI systems that are public authorities, or Union institutions, bodies, offices or agencies shall comply with the registration obligations referred to in Article 49. When such deployers find that the high-risk AI system that they envisage using has not been registered in the EU database referred to in Article 71, they shall not use that system and shall inform the provider or the distributor.
- Article 26(9): Where applicable, deployers of high-risk AI systems shall use the information provided under Article 13 of this Regulation to comply with their obligation to carry out a data protection impact assessment under Article 35 of Regulation (EU) 2016/679 or Article 27 of Directive (EU) 2016/680.
- Article 26(10): Without prejudice to Directive (EU) 2016/680, in the framework of an investigation for the targeted search of a person suspected or convicted of having committed a criminal offence, the deployer of a high-risk AI system for post-remote biometric identification shall request an authorisation, ex ante, or without undue delay and no later than 48 hours, by a judicial authority or an administrative authority whose decision is binding and subject to judicial review, for the use of that system, except when it is used for the initial identification of a potential suspect based on objective and verifiable facts directly linked to the offence. Each use shall be limited to what is strictly necessary for the investigation of a specific criminal offence. If the authorisation requested pursuant to the first subparagraph is rejected, the use of the post-remote biometric identification system linked to that requested authorisation shall be stopped with immediate effect and the personal data linked to the use of the high-risk AI system for which the authorisation was requested shall be deleted. In no case shall such high-risk AI system for post-remote biometric identification be used for law enforcement purposes in an untargeted way, without any link to a criminal offence, a criminal proceeding, a genuine and present or genuine and foreseeable threat of a criminal offence, or the search for a specific missing person. It shall be ensured that no decision that produces an adverse legal effect on a person may be taken by the law enforcement authorities based solely on the output of such post-remote biometric identification systems. This paragraph is without prejudice to Article 9 of Regulation (EU) 2016/679 and Article 10 of Directive (EU) 2016/680 for the processing of biometric data. Regardless of the purpose or deployer, each use of such high-risk AI systems shall be documented in the relevant police file and shall be made available to the relevant market surveillance authority and the national data protection authority upon request, excluding the disclosure of sensitive operational data related to law enforcement. This subparagraph shall be without prejudice to the powers conferred by Directive (EU) 2016/680 on supervisory authorities. Deployers shall submit annual reports to the relevant market surveillance and national data protection authorities on their use of post-remote biometric identification systems, excluding the disclosure of sensitive operational data related to law enforcement. The reports may be aggregated to cover more than one deployment. Member States may introduce, in accordance with Union law, more restrictive laws on the use of post-remote biometric identification systems.
- Article 26(11): Without prejudice to Article 50 of this Regulation, deployers of high-risk AI systems referred to in Annex III that make decisions or assist in making decisions related to natural persons shall inform the natural persons that they are subject to the use of the high-risk AI system. For high-risk AI systems used for law enforcement purposes Article 13 of Directive (EU) 2016/680 shall apply.
- Article 26(12): Deployers shall cooperate with the relevant competent authorities in any action those authorities take in relation to the high-risk AI system in order to implement this Regulation.
Sources: Article 26(1) (aiact:art_26:para_1@2024-08-01); Article 26(2) (aiact:art_26:para_2@2024-08-01); Article 26(3) (aiact:art_26:para_3@2024-08-01); Article 26(4) (aiact:art_26:para_4@2024-08-01); Article 26(5) (aiact:art_26:para_5@2024-08-01); Article 26(6) (aiact:art_26:para_6@2024-08-01); Article 26(7) (aiact:art_26:para_7@2024-08-01); Article 26(8) (aiact:art_26:para_8@2024-08-01); Article 26(9) (aiact:art_26:para_9@2024-08-01); Article 26(10) (aiact:art_26:para_10@2024-08-01); Article 26(11) (aiact:art_26:para_11@2024-08-01); Article 26(12) (aiact:art_26:para_12@2024-08-01)
```

</details>

## X02: Vague HR tool: it asks what it needs to know

> Our company is planning to use an AI tool in human resources to help with decisions about our staff. Which AI Act rules apply to us on 1 November 2026?

| Determination | Answer |
|---|---|
| Risk tier (Article 6) | could not be determined from the information provided. |
| Prohibited practices (Article 5) | could not be determined from the information provided. |
| Role (deployer) | you are a deployer of this system. |
| Role (provider) | could not be determined from the information provided. |
| Article 16 | could not be determined from the information provided. |
| Article 26 | could not be determined from the information provided. |

Cost: $0.0990 (3 API calls). Template fallback: no.

<details>
<summary>Full answer, unedited</summary>

```text
## Risk tier (Article 6): could not be determined from the information provided.
- Annex III(1)(a) distinguishes remote biometric identification from verification solely to confirm a claimed identity. Whether the HR tool performs remote biometric identification is therefore relevant to its classification under Article 6(2). [Article 6(2); Annex III(1)(a)]
- Annex III(1)(b) addresses biometric categorisation that infers sensitive or protected attributes, while Annex III(1)(c) addresses emotion recognition. Whether the HR tool performs either function matters to its classification; neither function is established by the description. [Annex III(1)(b); Annex III(1)(c)]
- Annex III(4)(a) addresses recruitment and selection, including filtering applications and evaluating candidates. The nature of the HR decisions is needed to establish whether the tool serves those purposes. [Annex III(4)(a)]
- Annex III(4)(b) addresses employment decisions, task allocation based on individual behaviour or characteristics, and performance or behaviour monitoring and evaluation. The tool's specific staff decisions, allocation criteria and monitoring functions matter because general staff decision support does not identify which of these purposes it serves. [Annex III(4)(b)]
To decide this, we would need to know: item 1 in the list at the end.
Sources: Article 6(2) (aiact:art_6:para_2@2024-08-01); Article 6(1)(a) (aiact:art_6:para_1:point_a@2024-08-01); Annex III(1)(a) (aiact:anx_III:area_1:item_a@2024-08-01); Annex III(1)(b) (aiact:anx_III:area_1:item_b@2024-08-01); Annex III(1)(c) (aiact:anx_III:area_1:item_c@2024-08-01); Annex III(4)(a) (aiact:anx_III:area_4:item_a@2024-08-01); Annex III(4)(b) (aiact:anx_III:area_4:item_b@2024-08-01)

## Prohibited practices (Article 5): could not be determined from the information provided.
- Article 5(1)(a) concerns subliminal, manipulative or deceptive techniques that distort informed decision-making and cause or are reasonably likely to cause significant harm. Details of the tool's techniques and effects are needed to establish whether those elements are present. [Article 5(1)(a)]
- Article 5(1)(b) concerns exploitation of vulnerabilities linked to age, disability or social or economic circumstances, combined with behavioural distortion and significant harm. Whether the tool exploits such vulnerabilities and with what effects matters to this provision. [Article 5(1)(b)]
- Article 5(1)(c) addresses social scoring that leads to detrimental treatment in unrelated contexts or treatment that is unjustified or disproportionate. The tool's scoring practices, the contexts of its data and the resulting treatment are needed to establish those elements. [Article 5(1)(c)]
- Article 5(1)(d) distinguishes criminal-offending predictions based solely on profiling or personality characteristics from support for human judgement grounded in objective, verifiable facts directly linked to criminal activity. Whether staff decisions involve such predictions, and their factual basis, matters to that distinction. [Article 5(1)(d)]
- Article 5(1)(f) addresses emotion inference in workplaces and educational institutions, with an exception for medical or safety reasons. The workplace context is identified, but whether the tool infers emotions and the purpose of any such use remain necessary details. [Article 5(1)(f)]
- Article 5(1)(g) addresses biometric categorisation used to infer specified sensitive characteristics and distinguishes certain activities involving lawfully acquired biometric datasets or law enforcement. Details of biometric data, inferred characteristics and categorisation practices are needed to establish the tool's relationship to this provision. [Article 5(1)(g)]
To decide this, we would need to know: item 2 in the list at the end.
Sources: Article 5(1)(e) (aiact:art_5:para_1:point_e@2024-08-01); Article 5(1)(h) (aiact:art_5:para_1:point_h@2024-08-01); Article 5(1)(a) (aiact:art_5:para_1:point_a@2024-08-01); Article 5(1)(b) (aiact:art_5:para_1:point_b@2024-08-01); Article 5(1)(c) (aiact:art_5:para_1:point_c@2024-08-01); Article 5(1)(d) (aiact:art_5:para_1:point_d@2024-08-01); Article 5(1)(f) (aiact:art_5:para_1:point_f@2024-08-01); Article 5(1)(g) (aiact:art_5:para_1:point_g@2024-08-01)

## Role (deployer): you are a deployer of this system.
Sources: Article 3(4) (aiact:art_3:point_4@2024-08-01)

## Role (provider): could not be determined from the information provided.
- Article 3(3) links the provider role to developing or commissioning a system and placing it on the market or putting it into service under one's own name or trademark. The organisation's development arrangements and branding therefore matter to its role. [Article 3(3)]
- Article 25(1)(a)–(c) addresses provider responsibilities arising from rebranding, substantial modification or changes of intended purpose, with the system's risk classification forming part of those conditions. Details of the organisation's branding and changes to the system's use are therefore relevant alongside that classification. [Article 25(1)(a); Article 25(1)(b); Article 25(1)(c)]
- Article 3(23) defines substantial modification by reference to unplanned changes after market placement or service entry that affect compliance or alter the assessed intended purpose. Whether the organisation changed the model, weights, training or safety-relevant components matters because those changes need to be considered against these criteria. [Article 3(23)]
To decide this, we would need to know: items 3, 4, 5, 6 and 7 in the list at the end.
Sources: Article 3(3) (aiact:art_3:point_3@2024-08-01); Article 25(1)(a) (aiact:art_25:para_1:point_a@2024-08-01); Article 25(1)(b) (aiact:art_25:para_1:point_b@2024-08-01); Article 3(23) (aiact:art_3:point_23@2024-08-01); Article 25(1)(c) (aiact:art_25:para_1:point_c@2024-08-01)

## Article 16: could not be determined from the information provided.
- The organisation's provider role and the system's risk classification are both prerequisites for determining its responsibilities under Article 16(a)–(l). Further detail on those two matters is needed to establish the organisation's obligations. [Article 16(a); Article 16(b); Article 16(c); Article 16(d); Article 16(e); Article 16(f); Article 16(g); Article 16(h); Article 16(i); Article 16(j); Article 16(k); Article 16(l)]
To decide this, we would need to know: items 4 and 8 in the list at the end.
Sources: Article 16(a) (aiact:art_16:point_a@2024-08-01); Article 16(b) (aiact:art_16:point_b@2024-08-01); Article 16(c) (aiact:art_16:point_c@2024-08-01); Article 16(d) (aiact:art_16:point_d@2024-08-01); Article 16(e) (aiact:art_16:point_e@2024-08-01); Article 16(f) (aiact:art_16:point_f@2024-08-01); Article 16(g) (aiact:art_16:point_g@2024-08-01); Article 16(h) (aiact:art_16:point_h@2024-08-01); Article 16(i) (aiact:art_16:point_i@2024-08-01); Article 16(j) (aiact:art_16:point_j@2024-08-01); Article 16(k) (aiact:art_16:point_k@2024-08-01); Article 16(l) (aiact:art_16:point_l@2024-08-01)

## Article 26: could not be determined from the information provided.
- The system's risk classification is a prerequisite for determining the organisation's responsibilities under Article 26(1)–(12). Further detail establishing that classification is needed to determine the obligations associated with its use of the system. [Article 26(1); Article 26(2); Article 26(3); Article 26(4); Article 26(5); Article 26(6); Article 26(7); Article 26(8); Article 26(9); Article 26(10); Article 26(11); Article 26(12)]
To decide this, we would need to know: item 4 in the list at the end.
Sources: Article 26(1) (aiact:art_26:para_1@2024-08-01); Article 26(2) (aiact:art_26:para_2@2024-08-01); Article 26(3) (aiact:art_26:para_3@2024-08-01); Article 26(4) (aiact:art_26:para_4@2024-08-01); Article 26(5) (aiact:art_26:para_5@2024-08-01); Article 26(6) (aiact:art_26:para_6@2024-08-01); Article 26(7) (aiact:art_26:para_7@2024-08-01); Article 26(8) (aiact:art_26:para_8@2024-08-01); Article 26(9) (aiact:art_26:para_9@2024-08-01); Article 26(10) (aiact:art_26:para_10@2024-08-01); Article 26(11) (aiact:art_26:para_11@2024-08-01); Article 26(12) (aiact:art_26:para_12@2024-08-01)

## What we would need to know
1. Is the AI system intended to be used as described in this Annex III item?
   Provisions: Annex III(1)(a); Annex III(1)(b); Annex III(1)(c); Annex III(4)(a); Annex III(4)(b)
   Needed for: Risk tier (Article 6)
2. Does the described practice fall under this prohibition, with none of the exceptions stated in the provision applying?
   Provisions: Article 5(1)(a); Article 5(1)(b); Article 5(1)(c); Article 5(1)(d); Article 5(1)(f); Article 5(1)(g)
   Needed for: Prohibited practices (Article 5)
3. Does the describing organisation develop / build the AI system (in-house, or have it built to its own specification)?
   Needed for: Role (provider)
4. depends on the risk tier, which is itself unresolved
   Needed for: Role (provider); Article 16; Article 26
5. Does the organisation market or present the system under its own name, brand or trademark?
   Needed for: Role (provider)
6. Has the organisation changed what the system is used for compared with how it was originally supplied?
   Needed for: Role (provider)
7. Has the organisation changed the system's model, weights, training, or safety-relevant components after receiving it?
   Needed for: Role (provider)
8. depends on the provider role, which is itself unresolved
   Needed for: Article 16
```

</details>

## X03: Bakery forecast before the Act was in force

> Our bakery uses an AI tool, bought from a software vendor and used as supplied, that forecasts how many loaves to bake each day from past sales. Which AI Act rules applied to us on 1 June 2024?

| Determination | Answer |
|---|---|
| Risk tier (Article 6) | no version of this provision was in force on 2024-06-01. The text entered into force on 2024-08-01. |
| Prohibited practices (Article 5) | no version of this provision was in force on 2024-06-01. The text entered into force on 2024-08-01. |
| Role (deployer) | no version of this provision was in force on 2024-06-01. The text entered into force on 2024-08-01. |
| Role (provider) | no version of this provision was in force on 2024-06-01. The text entered into force on 2024-08-01. |
| Article 16 | no version of this provision was in force on 2024-06-01. The text entered into force on 2024-08-01. |
| Article 26 | no version of this provision was in force on 2024-06-01. The text entered into force on 2024-08-01. |

Cost: $0.0067 (2 API calls). Template fallback: no.

<details>
<summary>Full answer, unedited</summary>

```text
## Risk tier (Article 6): no version of this provision was in force on 2024-06-01. The text entered into force on 2024-08-01.

## Prohibited practices (Article 5): no version of this provision was in force on 2024-06-01. The text entered into force on 2024-08-01.

## Role (deployer): no version of this provision was in force on 2024-06-01. The text entered into force on 2024-08-01.

## Role (provider): no version of this provision was in force on 2024-06-01. The text entered into force on 2024-08-01.

## Article 16: no version of this provision was in force on 2024-06-01. The text entered into force on 2024-08-01.

## Article 26: no version of this provision was in force on 2024-06-01. The text entered into force on 2024-08-01.
```

</details>

## X04: Inferring voters' political opinions from face photos

> Our political consultancy developed software that analyses photos of voters' faces to infer their political opinions, and we use the results to target campaign messages. Which AI Act rules apply to us on 1 November 2026?

**Important: a prohibited practice applies (Article 5). The described system may not be placed on the market, put into service or used for this practice. The high-risk classification and obligations below are listed for reference only; meeting them would not make the practice lawful.**

| Determination | Answer |
|---|---|
| Risk tier (Article 6) | high-risk. *(for reference only)* |
| Prohibited practices (Article 5) | the described practice falls under a prohibition. |
| Role (deployer) | you are a deployer of this system. |
| Role (provider) | you are a provider of this system. |
| Article 16 | applies to this case. *(for reference only)* |
| Article 26 | applies to this case. *(for reference only)* |

Cost: $0.0874 (3 API calls). Template fallback: no.

**Re-rendered on 2026-11-11** with the current renderer, at $0 and with no API call, from this example's recorded facts, matcher verdicts and model text. The model's words are unchanged. Only code-written lines differ from the recording: the prohibited-practice notice at the top and the "for reference only" marks, added after the evaluation so that the high-risk sections no longer read as a way to comply. The answer as originally recorded is in [`data/demo/recording.md`](../data/demo/recording.md#x04-a-prohibited-practice).

**Note:** the prohibition (Article 5(1)(g), biometric categorisation inferring political opinions) is the finding that matters. The high-risk classification and Articles 16 and 26 are still determined and shown, marked as for reference only. Some quoted Article 26 paragraphs do not fit this case, such as 26(10) on criminal investigations (see [limitations.md](limitations.md)). The prohibition is not affected by the 2026 amendment (Regulation (EU) 2026/1744).

<details>
<summary>Full answer (model text unedited)</summary>

```text
Important: a prohibited practice applies (Article 5). The described system may not be placed on the market, put into service or used for this practice. The high-risk classification and obligations below are listed for reference only; meeting them would not make the practice lawful.

## Risk tier (Article 6): high-risk.
(For reference only: a prohibited practice applies; see Prohibited practices (Article 5).)
- Article 6(2) links the system’s classification to the listed uses: biometric categorisation based on inferred sensitive or protected characteristics, and influencing election outcomes or voting behaviour. The described system fits both categories. [Article 6(2); Annex III(1)(b); Annex III(8)(b)]
- Article 6(3) distinguishes systems without significant risks or material influence on decisions, while retaining the classification for systems that profile natural persons. The findings do not establish grounds for departing from the listed-use classification. [Article 6(3), third subparagraph]
Sources: Article 6(2) (aiact:art_6:para_2@2024-08-01); Article 6(1)(a) (aiact:art_6:para_1:point_a@2024-08-01); Annex III(1)(b) (aiact:anx_III:area_1:item_b@2024-08-01); Annex III(8)(b) (aiact:anx_III:area_8:item_b@2024-08-01); Article 6(3), third subparagraph (aiact:art_6:para_3:subpara_3@2024-08-01)

## Prohibited practices (Article 5): the described practice falls under a prohibition.
- Article 5(1)(g) bars marketing, putting into service for this purpose, or using biometric categorisation systems that infer specified sensitive characteristics of individuals from biometric data. The described practice falls within this restriction on sensitive biometric inference. [Article 5(1)(g)]
Sources: Article 5(1)(g) (aiact:art_5:para_1:point_g@2024-08-01); Article 5(1)(a) (aiact:art_5:para_1:point_a@2024-08-01); Article 5(1)(b) (aiact:art_5:para_1:point_b@2024-08-01); Article 5(1)(c) (aiact:art_5:para_1:point_c@2024-08-01); Article 5(1)(d) (aiact:art_5:para_1:point_d@2024-08-01); Article 5(1)(e) (aiact:art_5:para_1:point_e@2024-08-01); Article 5(1)(f) (aiact:art_5:para_1:point_f@2024-08-01); Article 5(1)(h) (aiact:art_5:para_1:point_h@2024-08-01)

## Role (deployer): you are a deployer of this system.
Sources: Article 3(4) (aiact:art_3:point_4@2024-08-01)

## Role (provider): you are a provider of this system.
Sources: Article 3(3) (aiact:art_3:point_3@2024-08-01); Article 25(1)(a) (aiact:art_25:para_1:point_a@2024-08-01); Article 25(1)(b) (aiact:art_25:para_1:point_b@2024-08-01); Article 3(23) (aiact:art_3:point_23@2024-08-01); Article 25(1)(c) (aiact:art_25:para_1:point_c@2024-08-01)

## Article 16: applies to this case.
(For reference only: a prohibited practice applies; see Prohibited practices (Article 5).)
- For this system, the provider must ensure compliance with the system requirements, identify itself and its contact address, maintain a quality management system, retain documentation, and keep automatically generated logs under its control. [Article 16(a); Article 16(b); Article 16(c); Article 16(d); Article 16(e)]
- The provider must arrange the relevant conformity assessment before marketing or putting the system into service, draw up an EU declaration of conformity, affix the CE marking, and meet registration obligations. [Article 16(f); Article 16(g); Article 16(h); Article 16(i)]
- The provider must take necessary corrective action and provide the required information, demonstrate conformity upon a reasoned request from a national competent authority, and ensure compliance with accessibility requirements. [Article 16(j); Article 16(k); Article 16(l)]
- Article 16(a): Providers of high-risk AI systems shall: ensure that their high-risk AI systems are compliant with the requirements set out in Section 2;
- Article 16(b): Providers of high-risk AI systems shall: indicate on the high-risk AI system or, where that is not possible, on its packaging or its accompanying documentation, as applicable, their name, registered trade name or registered trade mark, the address at which they can be contacted;
- Article 16(c): Providers of high-risk AI systems shall: have a quality management system in place which complies with Article 17;
- Article 16(d): Providers of high-risk AI systems shall: keep the documentation referred to in Article 18;
- Article 16(e): Providers of high-risk AI systems shall: when under their control, keep the logs automatically generated by their high-risk AI systems as referred to in Article 19;
- Article 16(f): Providers of high-risk AI systems shall: ensure that the high-risk AI system undergoes the relevant conformity assessment procedure as referred to in Article 43, prior to its being placed on the market or put into service;
- Article 16(g): Providers of high-risk AI systems shall: draw up an EU declaration of conformity in accordance with Article 47;
- Article 16(h): Providers of high-risk AI systems shall: affix the CE marking to the high-risk AI system or, where that is not possible, on its packaging or its accompanying documentation, to indicate conformity with this Regulation, in accordance with Article 48;
- Article 16(i): Providers of high-risk AI systems shall: comply with the registration obligations referred to in Article 49(1);
- Article 16(j): Providers of high-risk AI systems shall: take the necessary corrective actions and provide information as required in Article 20;
- Article 16(k): Providers of high-risk AI systems shall: upon a reasoned request of a national competent authority, demonstrate the conformity of the high-risk AI system with the requirements set out in Section 2;
- Article 16(l): Providers of high-risk AI systems shall: ensure that the high-risk AI system complies with accessibility requirements in accordance with Directives (EU) 2016/2102 and (EU) 2019/882.
Sources: Article 16(a) (aiact:art_16:point_a@2024-08-01); Article 16(b) (aiact:art_16:point_b@2024-08-01); Article 16(c) (aiact:art_16:point_c@2024-08-01); Article 16(d) (aiact:art_16:point_d@2024-08-01); Article 16(e) (aiact:art_16:point_e@2024-08-01); Article 16(f) (aiact:art_16:point_f@2024-08-01); Article 16(g) (aiact:art_16:point_g@2024-08-01); Article 16(h) (aiact:art_16:point_h@2024-08-01); Article 16(i) (aiact:art_16:point_i@2024-08-01); Article 16(j) (aiact:art_16:point_j@2024-08-01); Article 16(k) (aiact:art_16:point_k@2024-08-01); Article 16(l) (aiact:art_16:point_l@2024-08-01)

## Article 26: applies to this case.
(For reference only: a prohibited practice applies; see Prohibited practices (Article 5).)
- The deployer must use technical and organisational measures to follow the system’s instructions and assign oversight to people with appropriate competence, training, authority and support. Data under the deployer’s control must be relevant and sufficiently representative for the system’s intended purpose; these duties preserve other legal obligations and organisational discretion over oversight resources. [Article 26(1); Article 26(2); Article 26(3); Article 26(4)]
- The deployer must monitor operation, notify the relevant parties of risks or serious incidents, and suspend use when the stated risk conditions arise. Automatically generated logs under its control must generally be retained for at least six months, with adjustments for other governing law and financial-sector arrangements. [Article 26(5); Article 26(6)]
- For workplace use, employers must inform workers’ representatives and affected workers beforehand; public-sector deployers must meet registration duties and refrain from using a system absent from the required database. Where a data protection impact assessment is required, the deployer must use the information provided for that purpose. [Article 26(7); Article 26(8); Article 26(9)]
- For post-remote biometric identification in criminal investigations, the deployer must observe the specified authorisation process, necessity limits, restrictions on untargeted use and solely automated adverse decisions, and documentation and reporting duties. Rejection of authorisation requires immediate cessation of the linked use and deletion of the associated personal data. [Article 26(10)]
- When the system makes or assists decisions concerning individuals, the deployer must inform those individuals of its use, with a distinct information regime for law enforcement. The deployer must also cooperate with competent authorities implementing the Regulation. [Article 26(11); Article 26(12)]
- Article 26(1): Deployers of high-risk AI systems shall take appropriate technical and organisational measures to ensure they use such systems in accordance with the instructions for use accompanying the systems, pursuant to paragraphs 3 and 6.
- Article 26(2): Deployers shall assign human oversight to natural persons who have the necessary competence, training and authority, as well as the necessary support.
- Article 26(3): The obligations set out in paragraphs 1 and 2, are without prejudice to other deployer obligations under Union or national law and to the deployer’s freedom to organise its own resources and activities for the purpose of implementing the human oversight measures indicated by the provider.
- Article 26(4): Without prejudice to paragraphs 1 and 2, to the extent the deployer exercises control over the input data, that deployer shall ensure that input data is relevant and sufficiently representative in view of the intended purpose of the high-risk AI system.
- Article 26(5): Deployers shall monitor the operation of the high-risk AI system on the basis of the instructions for use and, where relevant, inform providers in accordance with Article 72. Where deployers have reason to consider that the use of the high-risk AI system in accordance with the instructions may result in that AI system presenting a risk within the meaning of Article 79(1), they shall, without undue delay, inform the provider or distributor and the relevant market surveillance authority, and shall suspend the use of that system. Where deployers have identified a serious incident, they shall also immediately inform first the provider, and then the importer or distributor and the relevant market surveillance authorities of that incident. If the deployer is not able to reach the provider, Article 73 shall apply mutatis mutandis. This obligation shall not cover sensitive operational data of deployers of AI systems which are law enforcement authorities. For deployers that are financial institutions subject to requirements regarding their internal governance, arrangements or processes under Union financial services law, the monitoring obligation set out in the first subparagraph shall be deemed to be fulfilled by complying with the rules on internal governance arrangements, processes and mechanisms pursuant to the relevant financial service law.
- Article 26(6): Deployers of high-risk AI systems shall keep the logs automatically generated by that high-risk AI system to the extent such logs are under their control, for a period appropriate to the intended purpose of the high-risk AI system, of at least six months, unless provided otherwise in applicable Union or national law, in particular in Union law on the protection of personal data. Deployers that are financial institutions subject to requirements regarding their internal governance, arrangements or processes under Union financial services law shall maintain the logs as part of the documentation kept pursuant to the relevant Union financial service law.
- Article 26(7): Before putting into service or using a high-risk AI system at the workplace, deployers who are employers shall inform workers’ representatives and the affected workers that they will be subject to the use of the high-risk AI system. This information shall be provided, where applicable, in accordance with the rules and procedures laid down in Union and national law and practice on information of workers and their representatives.
- Article 26(8): Deployers of high-risk AI systems that are public authorities, or Union institutions, bodies, offices or agencies shall comply with the registration obligations referred to in Article 49. When such deployers find that the high-risk AI system that they envisage using has not been registered in the EU database referred to in Article 71, they shall not use that system and shall inform the provider or the distributor.
- Article 26(9): Where applicable, deployers of high-risk AI systems shall use the information provided under Article 13 of this Regulation to comply with their obligation to carry out a data protection impact assessment under Article 35 of Regulation (EU) 2016/679 or Article 27 of Directive (EU) 2016/680.
- Article 26(10): Without prejudice to Directive (EU) 2016/680, in the framework of an investigation for the targeted search of a person suspected or convicted of having committed a criminal offence, the deployer of a high-risk AI system for post-remote biometric identification shall request an authorisation, ex ante, or without undue delay and no later than 48 hours, by a judicial authority or an administrative authority whose decision is binding and subject to judicial review, for the use of that system, except when it is used for the initial identification of a potential suspect based on objective and verifiable facts directly linked to the offence. Each use shall be limited to what is strictly necessary for the investigation of a specific criminal offence. If the authorisation requested pursuant to the first subparagraph is rejected, the use of the post-remote biometric identification system linked to that requested authorisation shall be stopped with immediate effect and the personal data linked to the use of the high-risk AI system for which the authorisation was requested shall be deleted. In no case shall such high-risk AI system for post-remote biometric identification be used for law enforcement purposes in an untargeted way, without any link to a criminal offence, a criminal proceeding, a genuine and present or genuine and foreseeable threat of a criminal offence, or the search for a specific missing person. It shall be ensured that no decision that produces an adverse legal effect on a person may be taken by the law enforcement authorities based solely on the output of such post-remote biometric identification systems. This paragraph is without prejudice to Article 9 of Regulation (EU) 2016/679 and Article 10 of Directive (EU) 2016/680 for the processing of biometric data. Regardless of the purpose or deployer, each use of such high-risk AI systems shall be documented in the relevant police file and shall be made available to the relevant market surveillance authority and the national data protection authority upon request, excluding the disclosure of sensitive operational data related to law enforcement. This subparagraph shall be without prejudice to the powers conferred by Directive (EU) 2016/680 on supervisory authorities. Deployers shall submit annual reports to the relevant market surveillance and national data protection authorities on their use of post-remote biometric identification systems, excluding the disclosure of sensitive operational data related to law enforcement. The reports may be aggregated to cover more than one deployment. Member States may introduce, in accordance with Union law, more restrictive laws on the use of post-remote biometric identification systems.
- Article 26(11): Without prejudice to Article 50 of this Regulation, deployers of high-risk AI systems referred to in Annex III that make decisions or assist in making decisions related to natural persons shall inform the natural persons that they are subject to the use of the high-risk AI system. For high-risk AI systems used for law enforcement purposes Article 13 of Directive (EU) 2016/680 shall apply.
- Article 26(12): Deployers shall cooperate with the relevant competent authorities in any action those authorities take in relation to the high-risk AI system in order to implement this Regulation.
Sources: Article 26(1) (aiact:art_26:para_1@2024-08-01); Article 26(2) (aiact:art_26:para_2@2024-08-01); Article 26(3) (aiact:art_26:para_3@2024-08-01); Article 26(4) (aiact:art_26:para_4@2024-08-01); Article 26(5) (aiact:art_26:para_5@2024-08-01); Article 26(6) (aiact:art_26:para_6@2024-08-01); Article 26(7) (aiact:art_26:para_7@2024-08-01); Article 26(8) (aiact:art_26:para_8@2024-08-01); Article 26(9) (aiact:art_26:para_9@2024-08-01); Article 26(10) (aiact:art_26:para_10@2024-08-01); Article 26(11) (aiact:art_26:para_11@2024-08-01); Article 26(12) (aiact:art_26:para_12@2024-08-01)
```

</details>

## X05: Where it goes wrong: recruitment document check

> Our recruitment team uses DocCheck, a tool we license and use as supplied, that checks each job application for missing documents (CV, certificates, ID) and returns incomplete applications to the candidate before any recruiter reads them. Which AI Act rules apply to us on 1 November 2026?

| Determination | Answer |
|---|---|
| Risk tier (Article 6) | not high-risk. |
| Prohibited practices (Article 5) | no prohibited practice was found for this case. |
| Role (deployer) | you are a deployer of this system. |
| Role (provider) | you are not a provider of this system. |
| Article 16 | does not apply to this case. |
| Article 26 | does not apply to this case. |

Cost: $0.0732 (3 API calls). Template fallback: no.

**Shown because it goes wrong.** The answer is "not high-risk" through the Article 6(3) derogation. That is debatable: returning incomplete applications before any recruiter reads them is close to "filtering applications", which Annex III(4)(a) lists as high-risk, and a lawyer could go either way. The matcher also cited Article 6(3)(b) (improving a previously completed human activity), which nothing in the description supports. Every automatic check passed, because the checks verify that citations exist and quote the Act, not that applying them is correct. Also, "Article 26 does not apply" does not mean no obligations at all: provisions outside this tool's scope, such as Article 4 (AI literacy), can still apply. See [limitations.md](limitations.md).

<details>
<summary>Full answer, unedited</summary>

```text
## Risk tier (Article 6): not high-risk.
- Annex III(4)(a) identifies recruitment and selection uses, including analysing job applications and evaluating candidates. The system’s described use connects to this employment category. [Annex III(4)(a)]
- The system’s described functions align with narrow procedural tasks, improving previously completed human work, and preparatory tasks under Article 6(3)(a), (b) and (d). These provisions link those functions to the absence of significant harm to health, safety or fundamental rights, including through not materially influencing decision outcomes. [Article 6(3)(a); Article 6(3)(b); Article 6(3)(d)]
Sources: Article 6(2) (aiact:art_6:para_2@2024-08-01); Article 6(1)(a) (aiact:art_6:para_1:point_a@2024-08-01); Annex III(4)(a) (aiact:anx_III:area_4:item_a@2024-08-01); Article 6(3)(a) (aiact:art_6:para_3:point_a@2024-08-01); Article 6(3)(b) (aiact:art_6:para_3:point_b@2024-08-01); Article 6(3)(d) (aiact:art_6:para_3:point_d@2024-08-01)

## Prohibited practices (Article 5): no prohibited practice was found for this case.
- Article 5(1)(a)–(h) concerns specific AI practices. For this system, the relevant distinction is between its general use case and the particular elements of the practices listed in those provisions. [Article 5(1)(a); Article 5(1)(b); Article 5(1)(c); Article 5(1)(d); Article 5(1)(e); Article 5(1)(f); Article 5(1)(g); Article 5(1)(h)]
Sources: Article 5(1)(a) (aiact:art_5:para_1:point_a@2024-08-01); Article 5(1)(b) (aiact:art_5:para_1:point_b@2024-08-01); Article 5(1)(c) (aiact:art_5:para_1:point_c@2024-08-01); Article 5(1)(d) (aiact:art_5:para_1:point_d@2024-08-01); Article 5(1)(e) (aiact:art_5:para_1:point_e@2024-08-01); Article 5(1)(f) (aiact:art_5:para_1:point_f@2024-08-01); Article 5(1)(g) (aiact:art_5:para_1:point_g@2024-08-01); Article 5(1)(h) (aiact:art_5:para_1:point_h@2024-08-01)

## Role (deployer): you are a deployer of this system.
Sources: Article 3(4) (aiact:art_3:point_4@2024-08-01)

## Role (provider): you are not a provider of this system.
Sources: Article 3(3) (aiact:art_3:point_3@2024-08-01)

## Article 16: does not apply to this case.
Sources: Article 16(a) (aiact:art_16:point_a@2024-08-01); Article 16(b) (aiact:art_16:point_b@2024-08-01); Article 16(c) (aiact:art_16:point_c@2024-08-01); Article 16(d) (aiact:art_16:point_d@2024-08-01); Article 16(e) (aiact:art_16:point_e@2024-08-01); Article 16(f) (aiact:art_16:point_f@2024-08-01); Article 16(g) (aiact:art_16:point_g@2024-08-01); Article 16(h) (aiact:art_16:point_h@2024-08-01); Article 16(i) (aiact:art_16:point_i@2024-08-01); Article 16(j) (aiact:art_16:point_j@2024-08-01); Article 16(k) (aiact:art_16:point_k@2024-08-01); Article 16(l) (aiact:art_16:point_l@2024-08-01)

## Article 26: does not apply to this case.
Sources: Article 26(1) (aiact:art_26:para_1@2024-08-01); Article 26(2) (aiact:art_26:para_2@2024-08-01); Article 26(3) (aiact:art_26:para_3@2024-08-01); Article 26(4) (aiact:art_26:para_4@2024-08-01); Article 26(5) (aiact:art_26:para_5@2024-08-01); Article 26(6) (aiact:art_26:para_6@2024-08-01); Article 26(7) (aiact:art_26:para_7@2024-08-01); Article 26(8) (aiact:art_26:para_8@2024-08-01); Article 26(9) (aiact:art_26:para_9@2024-08-01); Article 26(10) (aiact:art_26:para_10@2024-08-01); Article 26(11) (aiact:art_26:para_11@2024-08-01); Article 26(12) (aiact:art_26:para_12@2024-08-01)
```

</details>
