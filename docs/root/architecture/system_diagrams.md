# System Architecture Diagrams

This document provides comprehensive architectural diagrams for the Open Resource Broker system, showing the complete system architecture and detailed implementation views.

## High-Level System Architecture

The following diagram shows the complete ORB architecture with all layers and components:

```mermaid
graph TD
    %% Interface Layer
    subgraph "Interface Layer"
        CLI[CLI Commands]
        API[REST API]
        MCP[MCP Server]
        SDK[Python SDK<br/>ORBClient<br/>Async Client API]
    end

    %% Application Layer
    subgraph "Application Layer"
        CQRS[CQRS Buses<br/>Commands & Queries]
        HANDLERS[Command/Query Handlers]
        APPSERVICES[Application Services<br/>Template Defaults Service]
    end

    %% Domain Layer
    subgraph "Domain Layer"
        AGG1[Request Aggregate]
        AGG2[Machine Aggregate]
        AGG3[Template Aggregate]
    end

    %% Infrastructure Layer
    subgraph "Infrastructure Layer"
        subgraph "Registry/Strategy/Factory"
            DI[DI Container]
            PREG[Provider Registry<br/>MULTI_CHOICE Mode]
            SREG[Scheduler Registry<br/>SINGLE_CHOICE Mode]
            STREG[Storage Registry<br/>SINGLE_CHOICE Mode]
        end

        subgraph "Provider Strategies"
            AWS[AWS Provider<br/>EC2Fleet, RunInstances<br/>SpotFleet, ASG]
            K8S[Kubernetes Provider<br/>Pods, Deployments<br/>Jobs, StatefulSets]
            OTHERPROV[Other Providers<br/>Provider1, Provider2, etc.]
        end

        subgraph "Scheduler Strategies"
            HF[HostFactory Scheduler<br/>IBM Symphony Integration]
            DEF[Default Scheduler<br/>New Integrations]
            LSF[LSF Scheduler<br/>Future Support]
        end

        subgraph "Storage Strategies"
            JSON[JSON Storage<br/>File-based]
            DYNAMO[DynamoDB Storage<br/>AWS NoSQL]
            SQL[SQL Storage<br/>Relational DB]
        end
    end

    %% External Systems (Grouped)
    subgraph "External Systems"
        subgraph "Cloud APIs"
            AWSAPI[AWS APIs<br/>EC2, AutoScaling, Spot]
            K8SAPI[Kubernetes APIs<br/>Pods, Deployments]
            OTHERCLOUD[Other CSP APIs<br/>Provider1, Provider2, etc.]
        end

        subgraph "Schedulers"
            SYMPHONY[IBM Symphony<br/>HostFactory]
            LSFAPI[LSF APIs<br/>IBM Spectrum LSF]
            CLOUDNATIVE[Cloud Native<br/>Direct API Integration]
        end

        subgraph "Storage Systems"
            FILESYSTEM[File Systems<br/>Local Files]
            SELFDB[Self-Hosted DBs<br/>PostgreSQL, MySQL]
            MANAGEDDB[Managed DBs<br/>RDS, DynamoDB]
        end
    end

    %% Connections
    CLI --> CQRS
    API --> CQRS
    MCP --> CQRS
    SDK --> CQRS

    CQRS --> HANDLERS
    HANDLERS --> APPSERVICES
    APPSERVICES --> AGG1
    APPSERVICES --> AGG2
    APPSERVICES --> AGG3

    %% Handlers use infrastructure via registries
    HANDLERS --> PREG
    HANDLERS --> SREG
    HANDLERS --> STREG

    %% DI orchestrates registries
    DI --> PREG
    DI --> SREG
    DI --> STREG

    %% Registries create strategies
    PREG -.-> AWS
    PREG -.-> K8S
    PREG -.-> OTHERPROV
    SREG -.-> HF
    SREG -.-> DEF
    SREG -.-> LSF
    STREG -.-> JSON
    STREG -.-> DYNAMO
    STREG -.-> SQL

    %% Strategies connect to external systems
    AWS --> AWSAPI
    K8S --> K8SAPI
    OTHERPROV --> OTHERCLOUD

    HF --> SYMPHONY
    DEF --> CLOUDNATIVE
    LSF --> LSFAPI

    JSON --> FILESYSTEM
    DYNAMO --> MANAGEDDB
    SQL --> SELFDB
    SQL --> MANAGEDDB

    %% SDK Discovery (automatic method exposure)
    SDK -.-> HANDLERS
    MCP -.-> SDK

    %% Styling (Switched Infrastructure and Domain Colors)
    classDef interface fill:#e3f2fd,stroke:#1976d2,stroke-width:2px
    classDef application fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px
    classDef domain fill:#fff3e0,stroke:#f57c00,stroke-width:2px
    classDef infrastructure fill:#e8f5e8,stroke:#388e3c,stroke-width:2px
    classDef external fill:#fafafa,stroke:#616161,stroke-width:2px

    class CLI,API,MCP,SDK interface
    class CQRS,HANDLERS,APPSERVICES application
    class AGG1,AGG2,AGG3 domain
    class DI,PREG,SREG,STREG,AWS,K8S,OTHERPROV,HF,DEF,LSF,JSON,DYNAMO,SQL infrastructure
    class AWSAPI,K8SAPI,OTHERCLOUD,SYMPHONY,LSFAPI,CLOUDNATIVE,FILESYSTEM,SELFDB,MANAGEDDB external
```

## AWS Provider Implementation

This diagram shows the detailed AWS provider implementation with all services and handlers:

```mermaid
graph TD
    %% Registry Entry Point
    PREG[Provider Registry<br/>MULTI_CHOICE]

    %% AWS Provider Implementation
    subgraph "AWS Provider Strategy"
        AWSSTRAT[AWS Strategy<br/>Orchestrator]
        
        subgraph "AWS Services"
            AWSINS[Instance Operation Service]
            AWSHEALTH[Health Check Service]
            AWSTEMPL[Template Validation Service]
            AWSINFRA[Infrastructure Discovery Service<br/>VPC, Subnet, Security Groups]
            AWSHAND[Handler Registry Service]
            AWSCAP[Capability Service]
            AWSNATIVE[Native Spec Service]
        end
        
        subgraph "AWS Handlers"
            EC2H[EC2Fleet Handler]
            RIH[RunInstances Handler]
            SFH[SpotFleet Handler]
            ASGH[ASG Handler]
        end
        
        AWSCLIENT[AWS Client<br/>Boto3 + Authentication]
    end

    %% External AWS Systems
    subgraph "AWS Cloud APIs"
        EC2API[EC2 APIs<br/>RunInstances, DescribeInstances]
        FLEETAPI[Fleet APIs<br/>EC2Fleet, SpotFleet]
        ASGAPI[AutoScaling APIs<br/>CreateAutoScalingGroup]
        VPCAPI[VPC APIs<br/>DescribeVpcs, DescribeSubnets]
        IAMAPI[IAM APIs<br/>GetRole, AssumeRole]
        SSMAPI[SSM APIs<br/>GetParameter, GetParameters]
    end

    %% Registry connection
    PREG -.-> AWSSTRAT

    %% AWS Provider flows
    AWSSTRAT --> AWSINS
    AWSSTRAT --> AWSHEALTH
    AWSSTRAT --> AWSTEMPL
    AWSSTRAT --> AWSINFRA
    AWSSTRAT --> AWSHAND
    AWSSTRAT --> AWSCAP
    AWSSTRAT --> AWSNATIVE

    AWSHAND --> EC2H
    AWSHAND --> RIH
    AWSHAND --> SFH
    AWSHAND --> ASGH

    EC2H --> AWSCLIENT
    RIH --> AWSCLIENT
    SFH --> AWSCLIENT
    ASGH --> AWSCLIENT

    %% AWS Client to specific APIs
    AWSCLIENT --> EC2API
    AWSCLIENT --> FLEETAPI
    AWSCLIENT --> ASGAPI
    AWSCLIENT --> VPCAPI
    AWSCLIENT --> IAMAPI
    AWSCLIENT --> SSMAPI

    %% Styling (Light Colors)
    classDef registry fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px
    classDef aws fill:#e3f2fd,stroke:#1976d2,stroke-width:2px
    classDef external fill:#fafafa,stroke:#616161,stroke-width:2px

    class PREG registry
    class AWSSTRAT,AWSINS,AWSHEALTH,AWSTEMPL,AWSINFRA,AWSHAND,AWSCAP,AWSNATIVE,EC2H,RIH,SFH,ASGH,AWSCLIENT aws
    class EC2API,FLEETAPI,ASGAPI,VPCAPI,IAMAPI,SSMAPI external
```

## Scheduler Strategies Implementation

This diagram shows the scheduler strategies and their components:

```mermaid
graph TD
    %% Registry Entry Point
    SREG[Scheduler Registry<br/>SINGLE_CHOICE]

    %% Scheduler Implementations
    subgraph "HostFactory Scheduler Strategy"
        HFSTRAT[HostFactory Strategy<br/>Orchestrator]
        
        subgraph "HostFactory Components"
            HFMAPPER[Field Mapper<br/>HF ↔ Internal Format]
            HFTRANS[HF Transformations<br/>Business Logic]
            HFVALID[HF Validator<br/>Input Validation]
            HFFORMAT[HF Formatter<br/>Output Generation]
        end
    end

    subgraph "Default Scheduler Strategy"
        DEFSTRAT[Default Strategy<br/>Orchestrator]
        
        subgraph "Default Components"
            DEFMAPPER[Identity Mapper<br/>No Transformation]
            DEFFORMAT[JSON Formatter<br/>Native Output]
            DEFVALID[Basic Validator<br/>Schema Validation]
        end
    end

    %% External Scheduler Systems
    subgraph "Scheduler Systems"
        SYMPHONY[IBM Symphony HostFactory<br/>Legacy Integration]
        CLOUDNATIVE[Cloud Native Services<br/>Direct API Integration]
    end

    %% Registry connections
    SREG -.-> HFSTRAT
    SREG -.-> DEFSTRAT

    %% Scheduler flows
    HFSTRAT --> HFMAPPER
    HFSTRAT --> HFTRANS
    HFSTRAT --> HFVALID
    HFSTRAT --> HFFORMAT

    DEFSTRAT --> DEFMAPPER
    DEFSTRAT --> DEFFORMAT
    DEFSTRAT --> DEFVALID

    %% External connections
    HFFORMAT --> SYMPHONY
    DEFFORMAT --> CLOUDNATIVE

    %% Styling (Light Colors)
    classDef registry fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px
    classDef hf fill:#e8f5e8,stroke:#388e3c,stroke-width:2px
    classDef default fill:#e1bee7,stroke:#8e24aa,stroke-width:2px
    classDef external fill:#fafafa,stroke:#616161,stroke-width:2px

    class SREG registry
    class HFSTRAT,HFMAPPER,HFTRANS,HFVALID,HFFORMAT hf
    class DEFSTRAT,DEFMAPPER,DEFFORMAT,DEFVALID default
    class SYMPHONY,CLOUDNATIVE external
```

## Storage Strategies Implementation

This diagram shows the storage strategies and their components:

```mermaid
graph TD
    %% Registry Entry Point
    STREG[Storage Registry<br/>SINGLE_CHOICE]

    %% Storage Implementations
    subgraph "JSON Storage Strategy"
        JSONSTRAT[JSON Strategy<br/>Orchestrator]
        
        subgraph "JSON Components"
            JSONFILE[File Manager<br/>Read/Write Operations]
            JSONSER[JSON Serializer<br/>Object ↔ JSON]
            JSONLOCK[Lock Manager<br/>File Locking]
            JSONTX[Memory Transaction Manager<br/>In-Memory Transactions]
        end
    end

    subgraph "DynamoDB Storage Strategy"
        DYNAMOSTRAT[DynamoDB Strategy<br/>Orchestrator]
        
        subgraph "DynamoDB Components"
            DYNAMOCLIENT[Client Manager<br/>AWS SDK Management]
            DYNAMOCONV[Converter<br/>Object ↔ DynamoDB]
            DYNAMOLOCK[Lock Manager<br/>Distributed Locking]
            DYNAMOTX[Transaction Manager<br/>DynamoDB Transactions]
        end
    end

    subgraph "SQL Storage Strategy"
        SQLSTRAT[SQL Strategy<br/>Orchestrator]
        
        subgraph "SQL Components"
            SQLCONN[Connection Manager<br/>Pool Management]
            SQLQUERY[Query Builder<br/>Dynamic SQL Generation]
            SQLSER[SQL Serializer<br/>Object ↔ Relational]
            SQLLOCK[Lock Manager<br/>Row/Table Locking]
        end
    end

    %% External Storage Systems
    subgraph "Storage Systems"
        FILESYSTEM[Local File System<br/>JSON Files]
        DYNAMODB[AWS DynamoDB<br/>NoSQL Database]
        POSTGRESQL[PostgreSQL<br/>Relational Database]
        MYSQL[MySQL<br/>Relational Database]
        SQLITE[SQLite<br/>Embedded Database]
    end

    %% Registry connections
    STREG -.-> JSONSTRAT
    STREG -.-> DYNAMOSTRAT
    STREG -.-> SQLSTRAT

    %% Storage flows
    JSONSTRAT --> JSONFILE
    JSONSTRAT --> JSONSER
    JSONSTRAT --> JSONLOCK
    JSONSTRAT --> JSONTX

    DYNAMOSTRAT --> DYNAMOCLIENT
    DYNAMOSTRAT --> DYNAMOCONV
    DYNAMOSTRAT --> DYNAMOLOCK
    DYNAMOSTRAT --> DYNAMOTX

    SQLSTRAT --> SQLCONN
    SQLSTRAT --> SQLQUERY
    SQLSTRAT --> SQLSER
    SQLSTRAT --> SQLLOCK

    %% External connections
    JSONFILE --> FILESYSTEM
    DYNAMOCLIENT --> DYNAMODB
    SQLCONN --> POSTGRESQL
    SQLCONN --> MYSQL
    SQLCONN --> SQLITE

    %% Styling (Light Colors)
    classDef registry fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px
    classDef json fill:#e8f5e8,stroke:#388e3c,stroke-width:2px
    classDef dynamo fill:#e3f2fd,stroke:#1976d2,stroke-width:2px
    classDef sql fill:#fff3e0,stroke:#f57c00,stroke-width:2px
    classDef external fill:#fafafa,stroke:#616161,stroke-width:2px

    class STREG registry
    class JSONSTRAT,JSONFILE,JSONSER,JSONLOCK,JSONTX json
    class DYNAMOSTRAT,DYNAMOCLIENT,DYNAMOCONV,DYNAMOLOCK,DYNAMOTX dynamo
    class SQLSTRAT,SQLCONN,SQLQUERY,SQLSER,SQLLOCK sql
    class FILESYSTEM,DYNAMODB,POSTGRESQL,MYSQL,SQLITE external
```

## Architecture Principles

### Clean Architecture Layers

The system follows Clean Architecture principles with clear layer separation:

- **Interface Layer**: CLI, REST API, MCP Server, Python SDK
- **Application Layer**: CQRS buses, handlers, application services
- **Domain Layer**: Aggregates (Request, Machine, Template)
- **Infrastructure Layer**: Registries, strategies, external integrations

### Registry Pattern

All strategies use a unified registry pattern:

- **Provider Registry**: MULTI_CHOICE mode (multiple providers simultaneously)
- **Scheduler Registry**: SINGLE_CHOICE mode (one scheduler at a time)
- **Storage Registry**: SINGLE_CHOICE mode (one storage strategy at a time)

### Strategy Pattern

Each registry manages strategies for different concerns:

- **Provider Strategies**: Cloud provider integrations (AWS, Kubernetes, etc.)
- **Scheduler Strategies**: Output format strategies (HostFactory, Default)
- **Storage Strategies**: Persistence strategies (JSON, DynamoDB, SQL)

### Dependency Injection

The DI Container orchestrates all registries and manages dependencies across the system, ensuring proper separation of concerns and testability.

## Full System Architecture

```mermaid
graph TD
    %% Interface Layer
    subgraph "Interface Layer"
        CLI[CLI Commands]
        API[REST API]
        MCP[MCP Server]
        SDK[Python SDK<br/>ORBClient<br/>Async Client API]
    end

    %% Application Layer
    subgraph "Application Layer"
        CQRS[CQRS Buses<br/>Commands & Queries]
        HANDLERS[Command/Query Handlers]
        APPSERVICES[Application Services<br/>Template Defaults Service]
    end

    %% Domain Layer
    subgraph "Domain Layer"
        AGG1[Request Aggregate]
        AGG2[Machine Aggregate]
        AGG3[Template Aggregate]
    end

    %% Infrastructure Layer
    subgraph "Infrastructure Layer"
        subgraph "Registry/Strategy/Factory"
            DI[DI Container]
            PREG[Provider Registry<br/>MULTI_CHOICE Mode]
            SREG[Scheduler Registry<br/>SINGLE_CHOICE Mode]
            STREG[Storage Registry<br/>SINGLE_CHOICE Mode]
        end

        subgraph "Provider Strategies"
            AWS[AWS Provider<br/>EC2Fleet, RunInstances<br/>SpotFleet, ASG]
            K8S[Kubernetes Provider<br/>Pods, Deployments<br/>Jobs, StatefulSets]
            OTHERPROV[Other Providers<br/>Provider1, Provider2, etc.]
        end

        subgraph "Scheduler Strategies"
            HF[HostFactory Scheduler<br/>IBM Symphony Integration]
            DEF[Default Scheduler<br/>New Integrations]
            LSF[LSF Scheduler<br/>Future Support]
        end

        subgraph "Storage Strategies"
            JSON[JSON Storage<br/>File-based]
            DYNAMO[DynamoDB Storage<br/>AWS NoSQL]
            SQL[SQL Storage<br/>Relational DB]
        end
    end

    %% External Systems (Grouped)
    subgraph "External Systems"
        subgraph "Cloud APIs"
            AWSAPI[AWS APIs<br/>EC2, AutoScaling, Spot]
            K8SAPI[Kubernetes APIs<br/>Pods, Deployments]
            OTHERCLOUD[Other CSP APIs<br/>Provider1, Provider2, etc.]
        end

        subgraph "Schedulers"
            SYMPHONY[IBM Symphony<br/>HostFactory]
            LSFAPI[LSF APIs<br/>IBM Spectrum LSF]
            CLOUDNATIVE[Cloud Native<br/>Direct API Integration]
        end

        subgraph "Storage Systems"
            FILESYSTEM[File Systems<br/>Local, NFS, S3]
            SELFDB[Self-Hosted DBs<br/>PostgreSQL, MySQL]
            MANAGEDDB[Managed DBs<br/>RDS, DynamoDB]
        end
    end

    %% Connections
    CLI --> CQRS
    API --> CQRS
    MCP --> CQRS
    SDK --> CQRS

    CQRS --> HANDLERS
    HANDLERS --> APPSERVICES
    APPSERVICES --> AGG1
    APPSERVICES --> AGG2
    APPSERVICES --> AGG3

    %% Handlers use infrastructure via registries
    HANDLERS --> PREG
    HANDLERS --> SREG
    HANDLERS --> STREG

    %% DI orchestrates registries
    DI --> PREG
    DI --> SREG
    DI --> STREG

    %% Registries create strategies
    PREG -.-> AWS
    PREG -.-> K8S
    PREG -.-> OTHERPROV
    SREG -.-> HF
    SREG -.-> DEF
    SREG -.-> LSF
    STREG -.-> JSON
    STREG -.-> DYNAMO
    STREG -.-> SQL

    %% Strategies connect to external systems
    AWS --> AWSAPI
    K8S --> K8SAPI
    OTHERPROV --> OTHERCLOUD

    HF --> SYMPHONY
    DEF --> CLOUDNATIVE
    LSF --> LSFAPI

    JSON --> FILESYSTEM
    DYNAMO --> MANAGEDDB
    SQL --> SELFDB
    SQL --> MANAGEDDB

    %% SDK Discovery (automatic method exposure)
    SDK -.-> HANDLERS
    MCP -.-> SDK

    %% Styling
    classDef interface fill:#e1f5fe,stroke:#01579b,stroke-width:2px
    classDef application fill:#f3e5f5,stroke:#4a148c,stroke-width:2px
    classDef domain fill:#fff3e0,stroke:#e65100,stroke-width:2px
    classDef infrastructure fill:#e8f5e8,stroke:#1b5e20,stroke-width:2px
    classDef external fill:#f5f5f5,stroke:#424242,stroke-width:2px

    class CLI,API,MCP,SDK interface
    class CQRS,HANDLERS,APPSERVICES application
    class AGG1,AGG2,AGG3,PORTS,EVENTS domain
    class DI,PREG,SREG,STREG,AWS,K8S,OTHERPROV,HF,DEF,LSF,JSON,DYNAMO,SQL infrastructure
    class AWSAPI,K8SAPI,OTHERCLOUD,SYMPHONY,LSFAPI,CLOUDNATIVE,FILESYSTEM,SELFDB,MANAGEDDB external
```
