#  Provider Strategy Guide
## Open Host Factory Plugin - Multi-Cloud Provider Management
## Updated: 2025-07-02

---

##  **OVERVIEW**

The Provider Strategy system enables runtime provider selection, automatic failover, and multi-cloud operations through a clean CQRS interface. This system activates the existing comprehensive provider strategy ecosystem (130KB of code) that was previously unused.

### **Key Benefits:**
- **Runtime Provider Selection**: Choose optimal provider for each operation
- **Automatic Failover**: Switch to backup providers on failure
- **Multi-Cloud Support**: Foundation for AWS, Azure, GCP, etc.
- **Load Balancing**: Distribute operations across multiple providers
- **Health Monitoring**: Continuous provider health assessment
- **Performance Optimization**: Select providers based on performance metrics

---

##  **ARCHITECTURE**

### **CQRS Integration:**
```
Interface Layer -> CQRS Commands/Queries -> Provider Strategy Handlers -> Provider Context -> Cloud Providers
```

### **Components:**
- **Commands**: Provider strategy operations (select, execute, register, configure)
- **Queries**: Provider information retrieval (health, capabilities, metrics)
- **Handlers**: CQRS handlers integrating with provider strategy ecosystem
- **Events**: Provider strategy events for monitoring and automation
- **Context**: Provider strategy management and execution

---

##  **GETTING STARTED**

### **1. Basic Provider Health Check**

Check the health of all providers:
```bash
python run.py getProviderHealth
```

Check specific provider health:
```bash
python run.py getProviderHealth --data '{"provider_name": "aws-primary"}'
```

### **2. List Available Providers**

List all available provider strategies:
```bash
python run.py listAvailableProviders
```

List only healthy providers:
```bash
python run.py listAvailableProviders --data '{"filter_healthy_only": true}'
```

### **3. Execute Provider Operations**

Execute operation with automatic provider selection:
```bash
python run.py executeProviderOperation --data '{
  "operation_type": "CREATE_INSTANCES",
  "parameters": {
    "template_id": "web-server",
    "count": 2
  }
}'
```

Execute operation with specific provider:
```bash
python run.py executeProviderOperation --data '{
  "operation_type": "CREATE_INSTANCES",
  "parameters": {
    "template_id": "web-server", 
    "count": 2
  },
  "strategy_override": "aws-primary"
}'
```

---

##  **PROVIDER OPERATIONS**

### **Available Operation Types:**
- `CREATE_INSTANCES`: Create new instances
- `TERMINATE_INSTANCES`: Terminate existing instances
- `GET_INSTANCE_STATUS`: Check instance status
- `VALIDATE_TEMPLATE`: Validate template configuration
- `GET_AVAILABLE_TEMPLATES`: List available templates
- `HEALTH_CHECK`: Perform provider health check

### **Provider Selection Criteria:**
```json
{
  "required_capabilities": ["instances", "load_balancers"],
  "min_success_rate": 95.0,
  "max_response_time_ms": 5000,
  "require_healthy": true,
  "exclude_strategies": ["aws-backup"],
  "prefer_strategies": ["aws-primary"]
}
```

---

##  **CONFIGURATION**

### **Provider Strategy Selection Policies:**
- `FIRST_AVAILABLE`: Use first available provider
- `ROUND_ROBIN`: Rotate between providers
- `WEIGHTED_ROUND_ROBIN`: Weighted rotation based on capacity
- `LEAST_CONNECTIONS`: Provider with fewest active operations
- `FASTEST_RESPONSE`: Provider with best response time
- `HIGHEST_SUCCESS_RATE`: Provider with best success rate
- `CAPABILITY_BASED`: Provider matching required capabilities
- `HEALTH_BASED`: Only healthy providers
- `RANDOM`: Random provider selection

### **Configure Provider Strategy:**
```bash
python run.py configureProviderStrategy --data '{
  "default_selection_policy": "CAPABILITY_BASED",
  "selection_criteria": {
    "min_success_rate": 95.0,
    "require_healthy": true
  },
  "fallback_strategies": ["aws-backup", "aws-secondary"],
  "health_check_interval": 300,
  "circuit_breaker_config": {
    "failure_threshold": 5,
    "recovery_timeout": 60
  }
}'
```

---

##  **MONITORING & METRICS**

### **Provider Health Monitoring**

Get comprehensive health status:
```bash
python run.py getProviderHealth --data '{
  "include_details": true,
  "include_history": true
}'
```

### **Performance Metrics**

Get provider performance metrics:
```bash
python run.py getProviderMetrics --data '{
  "provider_name": "aws-primary",
  "time_range_hours": 24,
  "include_operation_breakdown": true
}'
```

### **Provider Capabilities**

Get provider capabilities:
```bash
python run.py getProviderCapabilities --data '{
  "provider_name": "aws-primary",
  "include_performance_metrics": true,
  "include_limitations": true
}'
```

---

##  **INTEGRATION WITH EXISTING OPERATIONS**

### **Backward Compatibility**
All existing operations continue to work unchanged:
```bash
# These continue to work as before
python run.py getAvailableTemplates
python run.py requestMachines --data '{"template_id": "web", "machine_count": 2}'
python run.py getRequestStatus --request-id req-12345
```

### **Advanced Operations**
The provider strategy system enhances existing operations with:
- **Automatic provider selection** based on operation requirements
- **Failover support** if primary provider fails
- **Performance optimization** through provider selection
- **Health monitoring** of all provider operations

---

##  **ADVANCED USAGE**

### **Custom Provider Selection**

Select provider strategy for specific operation:
```bash
python run.py selectProviderStrategy --data '{
  "operation_type": "CREATE_INSTANCES",
  "selection_criteria": {
    "required_capabilities": ["spot_instances"],
    "max_response_time_ms": 3000,
    "prefer_strategies": ["aws-spot"]
  }
}'
```

### **Provider Registration**

Register new provider strategy:
```bash
python run.py registerProviderStrategy --data '{
  "strategy_name": "aws-west",
  "provider_type": "aws",
  "strategy_config": {
    "region": "us-west-2",
    "profile": "production"
  },
  "capabilities": {
    "instances": true,
    "spot_instances": true,
    "load_balancers": false
  },
  "priority": 1
}'
```

### **Health Status Updates**

Update provider health status:
```bash
python run.py updateProviderHealth --data '{
  "provider_name": "aws-primary",
  "health_status": {
    "is_healthy": false,
    "status_message": "Rate limit exceeded",
    "error_details": {
      "error_code": "RATE_LIMIT",
      "retry_after": 300
    }
  },
  "source": "monitoring_system"
}'
```

---

##  **TROUBLESHOOTING**

### **Common Issues**

#### **No Providers Available**
```bash
# Check provider registration
python run.py listAvailableProviders

# Check provider health
python run.py getProviderHealth
```

#### **Provider Selection Failures**
```bash
# Check selection criteria
python run.py selectProviderStrategy --data '{
  "operation_type": "CREATE_INSTANCES",
  "selection_criteria": {
    "require_healthy": false  # Relax health requirement
  }
}'
```

#### **Performance Issues**
```bash
# Check provider metrics
python run.py getProviderMetrics --data '{
  "time_range_hours": 1,
  "include_operation_breakdown": true,
  "include_error_details": true
}'
```

### **Debug Mode**
Enable debug logging for detailed provider strategy information:
```bash
export HF_LOG_LEVEL=DEBUG
python run.py executeProviderOperation --data '{"operation_type": "HEALTH_CHECK", "parameters": {}}'
```

---

##  **BEST PRACTICES**

### **1. Provider Strategy Configuration**
- Use `CAPABILITY_BASED` selection for production workloads
- Configure appropriate fallback strategies
- Set realistic health check intervals (5-10 minutes)
- Monitor provider metrics regularly

### **2. Health Monitoring**
- Implement automated health checks
- Set up alerts for provider failures
- Use circuit breaker patterns for resilience
- Monitor success rates and response times

### **3. Performance Optimization**
- Use performance-based selection for latency-sensitive operations
- Configure load balancing for high-throughput scenarios
- Monitor and adjust selection criteria based on metrics
- Implement caching for frequently accessed provider information

### **4. Multi-Cloud Strategy**
- Start with single provider, add others gradually
- Use consistent naming conventions across providers
- Implement provider-specific optimizations
- Plan for data consistency across providers

---

##  **FUTURE ENHANCEMENTS**

### **Planned Features:**
- **Azure Provider Strategy**: Microsoft Azure integration
- **GCP Provider Strategy**: Google Cloud Platform integration
- **Advanced Load Balancing**: Weighted algorithms with real-time metrics
- **Cost Optimization**: Provider selection based on cost metrics
- **Geographic Distribution**: Location-based provider selection
- **Auto-scaling Integration**: Dynamic provider capacity management

### **Integration Opportunities:**
- **Kubernetes Integration**: Provider strategies for container orchestration
- **Terraform Integration**: Infrastructure as code with provider strategies
- **Monitoring Integration**: Prometheus/Grafana dashboards
- **CI/CD Integration**: Provider strategies in deployment pipelines

---

##  **SUPPORT**

### **Getting Help:**
- Check the troubleshooting section above
- Review provider strategy logs in `logs/app.log`
- Use debug mode for detailed information
- Check provider health and metrics

### **Reporting Issues:**
- Include provider strategy configuration
- Provide relevant log entries
- Specify operation type and parameters
- Include provider health status

The Provider Strategy system transforms the Open Host Factory Plugin into a scalable and robust multi-cloud platform while maintaining full backward compatibility with existing operations.
