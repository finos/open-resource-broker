# HostFactory Deployment Scenarios

This document outlines various deployment scenarios for integrating the Open Host Factory Plugin with IBM Spectrum Symphony Host Factory, covering different environments, scales, and requirements.

## Deployment Architecture Overview

The plugin can be deployed in multiple configurations depending on your infrastructure requirements, scale, and operational preferences.

### Basic Deployment Components

- **HostFactory Plugin**: Core plugin executable and shell scripts
- **Configuration Files**: JSON configuration files for providers and settings
- **Storage Backend**: Data persistence layer (JSON files or SQL database)
- **Cloud Provider Access**: AWS credentials and network connectivity
- **Monitoring**: Logging and metrics collection

## Scenario 1: Single Node Development

### Use Case
- Development and testing environments
- Small-scale proof of concept
- Local development workflows

### Architecture
```
[Symphony Host Factory]  ->  [Plugin Scripts]  ->  [Plugin Core]  ->  [AWS]
                                 | 
                         [Local JSON Storage]
```

### Configuration
```json
{
  "version": "2.0.0",
  "provider": {
    "active_provider": "aws-dev",
    "providers": [
      {
        "name": "aws-dev",
        "type": "aws",
        "enabled": true,
        "config": {
          "region": "us-east-1",
          "profile": "development"
        }
      }
    ]
  },
  "storage": {
    "strategy": "json",
    "json_strategy": {
      "storage_type": "single_file",
      "base_path": "/opt/hostfactory-plugin/data"
    }
  }
}
```

### Deployment Steps
1. Install plugin on Symphony master node
2. Configure AWS credentials
3. Set up local JSON storage
4. Configure HostFactory to use plugin scripts
5. Test basic functionality

### Benefits
- Simple setup and configuration
- Easy debugging and development
- Minimal infrastructure requirements
- Fast iteration cycles

### Limitations
- Single point of failure
- Limited scalability
- No high availability
- Local storage only

## Scenario 2: Production Single Region

### Use Case
- Production workloads in single AWS region
- Medium-scale deployments (100-1000 machines)
- Standard high availability requirements

### Architecture
```
[Symphony Cluster]  ->  [Plugin on Master]  ->  [AWS Region]
                             | 
                    [RDS/DynamoDB Storage]
                             | 
                    [CloudWatch Monitoring]
```

### Configuration
```json
{
  "version": "2.0.0",
  "provider": {
    "active_provider": "aws-prod",
    "circuit_breaker": {
      "enabled": true,
      "failure_threshold": 5,
      "recovery_timeout": 300
    },
    "providers": [
      {
        "name": "aws-prod",
        "type": "aws",
        "enabled": true,
        "priority": 1,
        "config": {
          "region": "us-east-1",
          "profile": "production",
          "max_retries": 5,
          "timeout": 60,
          "handlers": {
            "types": {
              "ec2_fleet": "EC2Fleet",
              "spot_fleet": "SpotFleet",
              "asg": "ASG"
            }
          }
        }
      }
    ]
  },
  "storage": {
    "strategy": "sql",
    "sql_strategy": {
      "type": "postgresql",
      "host": "hostfactory-db.cluster-xyz.us-east-1.rds.amazonaws.com",
      "port": 5432,
      "name": "hostfactory",
      "pool_size": 10
    }
  },
  "logging": {
    "level": "INFO",
    "file_path": "/var/log/hostfactory-plugin/app.log",
    "max_size": 100,
    "backup_count": 10
  }
}
```

### Deployment Steps
1. Set up RDS PostgreSQL database
2. Configure IAM roles for EC2 instances
3. Install plugin on Symphony master nodes
4. Configure database connection
5. Set up CloudWatch logging
6. Configure monitoring and alerting
7. Test failover scenarios

### Benefits
- Production-ready reliability
- Persistent data storage
- Comprehensive monitoring
- Automated retry and recovery

### Limitations
- Single region dependency
- Master node single point of failure
- Limited disaster recovery options

## Scenario 3: Multi-Region High Availability

### Use Case
- Large-scale production deployments
- Disaster recovery requirements
- Global infrastructure with regional preferences
- High availability and fault tolerance

### Architecture
```
[Symphony Cluster]  ->  [Plugin HA Setup]
                            | 
    [AWS US-East-1]  <-   ->  [AWS US-West-2]  <-   ->  [AWS EU-West-1]
            |                      |                      | 
    [Regional Storage]   [Regional Storage]   [Regional Storage]
            |                      |                      | 
    [Regional Monitoring] [Regional Monitoring] [Regional Monitoring]
```

### Configuration
```json
{
  "version": "2.0.0",
  "provider": {
    "active_provider": "aws-primary",
    "selection_policy": "PRIORITY_FAILOVER",
    "health_check_interval": 30,
    "circuit_breaker": {
      "enabled": true,
      "failure_threshold": 3,
      "recovery_timeout": 180
    },
    "providers": [
      {
        "name": "aws-primary",
        "type": "aws",
        "enabled": true,
        "priority": 1,
        "weight": 100,
        "config": {
          "region": "us-east-1",
          "profile": "production"
        }
      },
      {
        "name": "aws-secondary",
        "type": "aws",
        "enabled": true,
        "priority": 2,
        "weight": 80,
        "config": {
          "region": "us-west-2",
          "profile": "production"
        }
      },
      {
        "name": "aws-tertiary",
        "type": "aws",
        "enabled": true,
        "priority": 3,
        "weight": 60,
        "config": {
          "region": "eu-west-1",
          "profile": "production"
        }
      }
    ]
  },
  "storage": {
    "strategy": "sql",
    "sql_strategy": {
      "type": "postgresql",
      "host": "hostfactory-global.cluster-xyz.amazonaws.com",
      "port": 5432,
      "name": "hostfactory_global",
      "pool_size": 20,
      "backup_enabled": true,
      "replication": {
        "enabled": true,
        "replicas": [
          "hostfactory-replica-west.cluster-abc.us-west-2.rds.amazonaws.com",
          "hostfactory-replica-eu.cluster-def.eu-west-1.rds.amazonaws.com"
        ]
      }
    }
  }
}
```

### Deployment Steps
1. Set up multi-region RDS with read replicas
2. Configure cross-region IAM roles
3. Deploy plugin on multiple Symphony nodes
4. Set up global load balancing
5. Configure cross-region monitoring
6. Implement automated failover procedures
7. Test disaster recovery scenarios

### Benefits
- High availability across regions
- Automatic failover capabilities
- Disaster recovery built-in
- Global scale support

### Limitations
- Complex setup and management
- Higher operational costs
- Cross-region latency considerations
- More complex troubleshooting

## Scenario 4: Containerized Deployment

### Use Case
- Kubernetes-based Symphony deployments
- Container orchestration environments
- Microservices architecture integration
- Cloud-native deployments

### Architecture
```
[Symphony K8s Cluster]
         | 
[HostFactory Plugin Pod]  ->  [AWS]
         | 
[ConfigMap/Secrets]  ->  [Persistent Volume]
         | 
[Service Mesh/Ingress]
```

### Kubernetes Configuration
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: hostfactory-plugin
  namespace: symphony
spec:
  replicas: 3
  selector:
    matchLabels:
      app: hostfactory-plugin
  template:
    metadata:
      labels:
        app: hostfactory-plugin
    spec:
      containers:
      - name: plugin
        image: hostfactory-plugin:latest
        ports:
        - containerPort: 8000
        env:
        - name: CONFIG_PATH
          value: "/app/config/config.json"
        - name: AWS_REGION
          value: "us-east-1"
        volumeMounts:
        - name: config
          mountPath: /app/config
        - name: data
          mountPath: /app/data
      volumes:
      - name: config
        configMap:
          name: hostfactory-config
      - name: data
        persistentVolumeClaim:
          claimName: hostfactory-data
---
apiVersion: v1
kind: Service
metadata:
  name: hostfactory-plugin-service
spec:
  selector:
    app: hostfactory-plugin
  ports:
  - port: 8000
    targetPort: 8000
  type: ClusterIP
```

### Benefits
- Container orchestration benefits
- Easy scaling and updates
- Integration with cloud-native tools
- Consistent deployment across environments

### Limitations
- Kubernetes complexity
- Container networking considerations
- Persistent storage challenges
- Additional operational overhead

## Scenario 5: Hybrid Cloud Deployment

### Use Case
- Multi-cloud strategies
- Vendor diversification
- Regulatory compliance requirements
- Cost optimization across providers

### Architecture
```
[Symphony Host Factory]
         | 
[Plugin with Multi-Provider Support]
         | 
[AWS]  <-   ->  [Azure]  <-   ->  [On-Premises]
```

### Configuration
```json
{
  "version": "2.0.0",
  "provider": {
    "active_provider": "aws-primary",
    "selection_policy": "COST_OPTIMIZED",
    "providers": [
      {
        "name": "aws-primary",
        "type": "aws",
        "enabled": true,
        "priority": 1,
        "config": {
          "region": "us-east-1",
          "cost_threshold": 0.10
        }
      },
      {
        "name": "azure-secondary",
        "type": "azure",
        "enabled": true,
        "priority": 2,
        "config": {
          "region": "eastus",
          "cost_threshold": 0.12
        }
      },
      {
        "name": "onprem-fallback",
        "type": "vmware",
        "enabled": true,
        "priority": 3,
        "config": {
          "vcenter_host": "vcenter.company.com",
          "datacenter": "DC1"
        }
      }
    ]
  }
}
```

### Benefits
- Vendor diversification
- Cost optimization opportunities
- Regulatory compliance flexibility
- Risk mitigation

### Limitations
- Complex management
- Multiple skill sets required
- Integration challenges
- Increased operational complexity

## Deployment Best Practices

### Security
- Use IAM roles instead of access keys
- Implement least privilege access
- Enable encryption at rest and in transit
- Regular security audits and updates

### Monitoring
- Comprehensive logging strategy
- Real-time alerting for failures
- Performance metrics collection
- Regular health checks

### Backup and Recovery
- Regular configuration backups
- Database backup strategies
- Disaster recovery procedures
- Recovery time objectives (RTO) planning

### Scaling
- Horizontal scaling capabilities
- Load balancing strategies
- Auto-scaling configurations
- Performance optimization

### Maintenance
- Regular updates and patches
- Configuration management
- Change control procedures
- Documentation maintenance

## Troubleshooting Common Deployment Issues

### Configuration Issues
- Validate JSON syntax
- Check file permissions
- Verify AWS credentials
- Test network connectivity

### Performance Issues
- Monitor resource utilization
- Optimize database queries
- Tune connection pools
- Review timeout settings

### Integration Issues
- Verify HostFactory configuration
- Check script permissions
- Test API endpoints
- Validate data formats

### Scaling Issues
- Monitor concurrent requests
- Check resource limits
- Review provider quotas
- Optimize batch operations

Each deployment scenario should be carefully evaluated based on your specific requirements, constraints, and operational capabilities. Consider starting with simpler scenarios and gradually moving to more complex deployments as your experience and requirements grow.
