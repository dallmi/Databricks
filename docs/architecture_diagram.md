# Architektur — Cross-Channel Communication Analytics

Visualisierung der Datenflüsse und Join-Keys zwischen Bronze, Silver, Gold und Dashboard.
Begleitet [BRD_cross_channel_analytics.md](BRD_cross_channel_analytics.md) und
[genie_questions_imep.md](genie_questions_imep.md).

---

## 1. End-to-End Data Flow (Layers)

```mermaid
flowchart LR
    subgraph Bronze["🟤 Bronze — raw source tables"]
        direction TB
        IM1[imep_bronze.tbl_email<br/>Master + TrackingId]
        IM2[imep_bronze.tbl_analytics_link<br/>Opens/Clicks + Agent]
        IM3[imep_bronze.tbl_email_receiver_status<br/>Sends/Bounces]
        IM4[imep_bronze.tbl_email_links<br/>Template links]
        IM5[imep_bronze.tbl_hr_employee<br/>T_NUMBER + WORKER_ID]
        IM6[imep_bronze.tbl_hr_user<br/>UbsId = T-Number]
        IM7[imep_bronze.tbl_hr_costcenter<br/>Division/Region]
        SP1[sharepoint_bronze.pages<br/>Inventory + UBSGICTrackingID]
        SP2[sharepoint_bronze.pageviews<br/>Interactions + GICTrackingID]
        CP1[sharepoint_cplan.*<br/>tracking_id planning]
    end

    subgraph Gold_Ext["🟡 iMEP Gold — pre-aggregated master"]
        direction TB
        GE1[imep_gold.tbl_pbi_platform_mailings<br/>Mailing master + TrackingId]
        GE2[imep_gold.tbl_pbi_platform_events<br/>Event master + registration count]
    end

    subgraph Silver["⚪ Silver — our harmonised model"]
        direction TB
        S1[silver.dim_pack<br/>pack metadata]
        S2[silver.dim_page<br/>article metadata]
        S3[silver.dim_employee_temporal<br/>T_NUMBER + HR dims]
        S4[silver.fact_email<br/>per-recipient-event]
        S5[silver.fact_page_view<br/>per-view + t_number]
        S6[silver.fact_event<br/>registration count]
    end

    subgraph GoldX["🟨 Gold — cross-channel"]
        direction TB
        GX1[gold.fact_cross_channel<br/>1 row per pack + channel KPIs]
    end

    DASH[📊 dashboard/multi_channel.html]

    IM1 --> GE1
    IM3 --> GE1
    IM4 --> GE1
    GE1 --> S1
    GE2 --> S1
    GE2 --> S6
    CP1 -.optional enrichment.-> S1

    IM1 --> S4
    IM2 --> S4
    IM3 --> S4
    IM5 --> S4
    IM6 --> S4
    IM7 --> S4

    IM5 --> S3
    IM7 --> S3

    SP1 --> S2
    SP2 --> S5
    S2 --> S5
    IM5 -.GPN→T_NUMBER bridge.-> S5
    S3 --> S5

    S1 --> GX1
    S4 --> GX1
    S5 --> GX1
    S6 --> GX1

    GX1 --> DASH

    classDef bronze fill:#ECEBE4,stroke:#8E8D83,color:#404040;
    classDef gold fill:#F5F0E1,stroke:#B98E2C,color:#404040;
    classDef silver fill:#F7F7F5,stroke:#7A7870,color:#404040;
    classDef goldx fill:#F5F0E1,stroke:#946F29,color:#404040;
    classDef dashboard fill:#FFFFFF,stroke:#E60000,color:#E60000,stroke-width:2px;
    class IM1,IM2,IM3,IM4,IM5,IM6,IM7,SP1,SP2,CP1 bronze;
    class GE1,GE2 gold;
    class S1,S2,S3,S4,S5,S6 silver;
    class GX1 goldx;
    class DASH dashboard;
```

---

## 2. iMEP — Bronze-Join-Patterns (Pattern 2 aus Genie-Code)

So baut sich `silver.fact_email`: eine Row pro Empfänger-Interaktion, angereichert mit HR und Mailing-Master.

```mermaid
erDiagram
    tbl_email ||--o{ tbl_email_receiver_status : "EmailId = Id"
    tbl_email ||--o{ tbl_analytics_link : "EmailId = Id"
    tbl_email_receiver_status ||--o{ tbl_analytics_link : "Id = EmailReceiverStatusId"
    tbl_email_receiver_status }o--|| tbl_hr_employee : "TNumber = T_NUMBER"
    tbl_analytics_link }o--|| tbl_hr_employee : "TNumber = T_NUMBER"
    tbl_hr_employee }o--|| tbl_hr_costcenter : "ORGANIZATIONAL_UNIT"
    tbl_hr_employee }o--|| tbl_hr_user : "T_NUMBER = UbsId (case-norm)"
    tbl_email ||--o{ tbl_email_links : "EmailId (template)"

    tbl_email {
        string Id PK
        string TrackingId "32-char 5-segment"
        string Title
        string Subject
        string CreatedBy "T_NUMBER"
        timestamp CreationDate
    }
    tbl_email_receiver_status {
        string Id PK
        string EmailId FK
        string TNumber "recipient t######"
        string Receiver "email addr"
        int Status
        string LogStatus "Open etc"
        timestamp DateTime "send time"
    }
    tbl_analytics_link {
        string Id PK
        string EmailId FK
        string EmailReceiverStatusId FK
        string TNumber "recipient"
        string Agent "desktop/mobile"
        string LinkTypeEnum "OPEN vs click"
        string CurrentLanguage
        timestamp CreationDate "event time"
    }
    tbl_hr_employee {
        string T_NUMBER PK "t###### lowercase"
        string WORKER_ID "= GPN, 8-digit"
        string ORGANIZATIONAL_UNIT
    }
    tbl_hr_costcenter {
        string ORGANIZATIONAL_UNIT PK
        string DIVISION
        string AREA
        string REGION
        string COUNTRY
    }
    tbl_hr_user {
        string UbsId "T-number UPPERCASE"
        string Town
    }
    tbl_email_links {
        string Id PK
        string ElementValueId
        string Url
        string LinkLabel
    }
```

**Zentrale Join-Kette für `silver.fact_email`** (vereinfacht):

```
FROM       tbl_analytics_link a
LEFT JOIN  tbl_email_receiver_status c  ON a.EmailReceiverStatusId = c.Id
LEFT JOIN  tbl_email b                  ON a.EmailId               = b.Id
LEFT JOIN  tbl_hr_employee hr           ON a.TNumber               = hr.T_NUMBER
LEFT JOIN  tbl_hr_costcenter cc         ON hr.ORGANIZATIONAL_UNIT  = cc.ORGANIZATIONAL_UNIT
LEFT JOIN  tbl_hr_user u                ON LOWER(hr.T_NUMBER)      = LOWER(u.UbsId)
WHERE      a.IsActive = 1
```

---

## 3. Employee-Identity-Bridge (GPN ↔ TNumber)

Das einzige Stelle, an der GPN im Modell lebt: beim Enrichment der PageView-Telemetrie. Danach nur noch TNumber.

```mermaid
flowchart LR
    AI[sharepoint_bronze.pageviews<br/>user_gpn: 00100200]
    HR[imep_bronze.tbl_hr_employee<br/>T_NUMBER = t100200<br/>WORKER_ID = 00100200]
    FPV[silver.fact_page_view<br/>t_number: t100200<br/>no GPN]

    AI -- "JOIN ON gpn = WORKER_ID" --> HR
    HR -- "emit LOWER(T_NUMBER) as t_number" --> FPV

    classDef src fill:#ECEBE4,stroke:#8E8D83;
    classDef hr fill:#F5F0E1,stroke:#B98E2C;
    classDef silver fill:#F7F7F5,stroke:#7A7870;
    class AI src;
    class HR hr;
    class FPV silver;
```

| Identifier | Ort | Format |
|---|---|---|
| `user_gpn` in pageviews | `sharepoint_bronze.pageviews` | `00100200` (8-digit) |
| `WORKER_ID` (= GPN) | `imep_bronze.tbl_hr_employee` | `00100200` |
| `T_NUMBER` | `imep_bronze.tbl_hr_employee` | `t100200` (lowercase) |
| `TNumber` in iMEP | `tbl_email_receiver_status`, `tbl_analytics_link` | `t100200` |
| `UbsId` | `imep_bronze.tbl_hr_user` | `T100200` (UPPERCASE) |

→ Nach Silver-Build: einheitlich `t_number` (lowercase), überall. GPN gelöscht.

---

## 4. Cross-Channel-Join über TrackingId

TrackingId ist der einzige kanalübergreifende Key — 32 Zeichen, 5 Segmente:

```
QRREP-0000058-240709-0000060-EMI
  │       │       │       │     └── channel:  EMI / INT / EVT / BAN
  │       │       │       └──────── activity sequence
  │       │       └──────────────── YYMMDD
  │       └──────────────────────── pack number
  └──────────────────────────────── cluster
  └───────┬───────┘
   tracking_pack_id  ← Dashboard-Grain
```

**Namens-Varianten pro System** (Silver harmonisiert sie zu `tracking_id`):

```mermaid
flowchart LR
    A[imep_bronze.tbl_email<br/>TrackingId]
    B[imep_gold.tbl_pbi_platform_mailings<br/>TrackingId]
    C[imep_gold.tbl_pbi_platform_events<br/>TrackingId]
    D[sharepoint_bronze.pages<br/>UBSGICTrackingID]
    E[sharepoint_bronze.pageviews<br/>GICTrackingID]
    F[sharepoint_cplan.*<br/>tracking_id]
    
    SILVER[silver.* <br/>tracking_id<br/>+ tracking_pack_id<br/>+ tracking_channel_abbr]

    A --> SILVER
    B --> SILVER
    C --> SILVER
    D --> SILVER
    E --> SILVER
    F --> SILVER

    classDef src fill:#ECEBE4,stroke:#8E8D83;
    classDef silver fill:#F7F7F5,stroke:#7A7870,stroke-width:2px;
    class A,B,C,D,E,F src;
    class SILVER silver;
```

---

## 5. Gold Cross-Channel Fact — der Funnel pro Pack

```mermaid
flowchart TB
    DP[silver.dim_pack<br/>1 row per pack]
    FE[silver.fact_email<br/>sent/opened/clicked aggregation]
    FP[silver.fact_page_view<br/>views/UV/TOS aggregation]
    FV[silver.fact_event<br/>registration count]
    
    GX[gold.fact_cross_channel<br/>1 row per tracking_pack_id]

    DP -- pack_id --> GX
    FE -- "GROUP BY tracking_pack_id" --> GX
    FP -- "GROUP BY tracking_pack_id" --> GX
    FV -- "GROUP BY tracking_pack_id" --> GX

    subgraph KPIs["Gold KPIs per pack"]
        K1[email_sent<br/>email_opened<br/>email_clicked]
        K2[page_views<br/>unique_readers<br/>avg_time_on_page]
        K3[event_registered]
    end

    GX --> KPIs

    classDef silver fill:#F7F7F5,stroke:#7A7870;
    classDef gold fill:#F5F0E1,stroke:#946F29,stroke-width:2px;
    classDef kpi fill:#FFFFFF,stroke:#E60000;
    class DP,FE,FP,FV silver;
    class GX gold;
    class K1,K2,K3 kpi;
```

**Funnel auf Pack-Ebene:**

```
Sent (100%) ─► Opened (~45%) ─► Clicked (~8%) ─► Page View (~6%) ─► Event Registered (~1.5%)
                                                      │
                                              nur Article-Pages
                                        (UBSGICTrackingID populated)
```

---

## 6. Dashboard-Grain-Hierarchie

```mermaid
flowchart TD
    CL[Cluster<br/>z.B. QRREP]
    PA[Pack<br/>tracking_pack_id<br/>QRREP-0000058]
    AC[Activity<br/>tracking_id <br/>QRREP-0000058-240709-0000060-EMI]

    CL --> PA
    PA --> AC

    D1[Dashboard Default-Grain<br/>1 row per Pack]
    PA --> D1

    classDef lvl1 fill:#F5F0E1,stroke:#B98E2C;
    classDef lvl2 fill:#F7F7F5,stroke:#7A7870,stroke-width:2px;
    classDef lvl3 fill:#ECEBE4,stroke:#8E8D83;
    classDef dash fill:#FFFFFF,stroke:#E60000,stroke-width:2px;
    class CL lvl1;
    class PA lvl2;
    class AC lvl3;
    class D1 dash;
```

Default-View zeigt eine Row pro Pack (`tracking_pack_id`). Drill-Down zur Activity-Ebene (`tracking_id`) optional. Roll-up zum Cluster für Programm-Reports.

---

## 7. Coverage Disclaimer (Q15-Findings)

```mermaid
flowchart LR
    ALL[Alle Intranet PageViews<br/>100%]
    ART[Article PageViews<br/>News + Events only]
    TRK[PageViews mit TrackingId<br/>= im Funnel]
    
    ALL --> ART
    ART --> TRK

    N[Dashboard-Metrik:<br/>'Attribuierte Views'<br/>deckt nur TRK ab]
    TRK --> N

    classDef all fill:#ECEBE4,stroke:#8E8D83;
    classDef art fill:#F5F0E1,stroke:#B98E2C;
    classDef trk fill:#F7F7F5,stroke:#7A7870,stroke-width:2px;
    classDef note fill:#FFFFFF,stroke:#E60000;
    class ALL all;
    class ART art;
    class TRK trk;
    class N note;
```

Q15/Q15b quantifizieren den Anteil TRK pro Monat → Default-Zeitraum und Coverage-Note im Dashboard.
