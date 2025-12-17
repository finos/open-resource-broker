#!/usr/bin/env node
/**
 * Example Node.js MCP client for Open Resource Broker.
 *
 * This example demonstrates how to connect to the MCP server and perform
 * common infrastructure provisioning tasks.
 *
 * Install dependencies:
 * npm install @modelcontextprotocol/sdk
 */

const { Client } = require('@modelcontextprotocol/sdk/client/index.js');
const { StdioClientTransport } = require('@modelcontextprotocol/sdk/client/stdio.js');

class HostFactoryMCPClient {
  constructor() {
    this.transport = new StdioClientTransport({
      command: 'orb',
      args: ['mcp', 'serve', '--stdio']
    });

    this.client = new Client({
      name: "hostfactory-nodejs-client",
      version: "1.0.0"
    }, {
      capabilities: {}
    });
  }

  async connect() {
    console.log('Connecting to MCP server...');
    await this.client.connect(this.transport);
    console.log('Connected successfully');
  }

  async disconnect() {
    console.log('Disconnecting from MCP server...');
    await this.client.close();
    console.log('Disconnected');
  }

  async listTools() {
    console.log('Listing available tools...');
    const result = await this.client.listTools();
    console.log(`Found ${result.tools.length} tools:`, result.tools.map(t => t.name));
    return result.tools;
  }

  async listResources() {
    console.log('Listing available resources...');
    const result = await this.client.listResources();
    console.log(`Found ${result.resources.length} resources:`, result.resources.map(r => r.uri));
    return result.resources;
  }

  async listPrompts() {
    console.log('Listing available prompts...');
    const result = await this.client.listPrompts();
    console.log(`Found ${result.prompts.length} prompts:`, result.prompts.map(p => p.name));
    return result.prompts;
  }

  async callTool(name, arguments = {}) {
    console.log(`Calling tool: ${name} with arguments:`, arguments);
    const result = await this.client.callTool({
      name: name,
      arguments: arguments
    });
    console.log('Tool result:', result.content);
    return result;
  }

  async readResource(uri) {
    console.log(`Reading resource: ${uri}`);
    const result = await this.client.readResource({ uri: uri });
    console.log('Resource content:', result.contents);
    return result;
  }

  async getPrompt(name, arguments = {}) {
    console.log(`Getting prompt: ${name} with arguments:`, arguments);
    const result = await this.client.getPrompt({
      name: name,
      arguments: arguments
    });
    console.log('Prompt result:', result);
    return result;
  }
}

async function exampleBasicOperations() {
  console.log('=== Basic MCP Operations Example ===');

  const client = new HostFactoryMCPClient();

  try {
    await client.connect();

    // List capabilities
    const tools = await client.listTools();
    const resources = await client.listResources();
    const prompts = await client.listPrompts();

    console.log('Basic operations completed successfully');

  } catch (error) {
    console.error('Error in basic operations:', error);
  } finally {
    await client.disconnect();
  }
}

async function exampleInfrastructureProvisioning() {
  console.log('=== Infrastructure Provisioning Example ===');

  const client = new HostFactoryMCPClient();

  try {
    await client.connect();

    // Step 1: List available providers
    const providers = await client.callTool('list_providers');

    // Step 2: Check provider health
    const health = await client.callTool('check_provider_health');

    // Step 3: List available templates
    const templates = await client.callTool('list_templates');

    // Step 4: Request infrastructure (example - commented out to avoid actual provisioning)
    // const requestResult = await client.callTool('request_machines', {
    //   template_id: 'EC2FleetInstant',
    //   count: 2
    // });

    console.log('Infrastructure provisioning workflow completed');

  } catch (error) {
    console.error('Error in infrastructure provisioning:', error);
  } finally {
    await client.disconnect();
  }
}

async function exampleResourceAccess() {
  console.log('=== Resource Access Example ===');

  const client = new HostFactoryMCPClient();

  try {
    await client.connect();

    // Read templates resource
    const templates = await client.readResource('templates://');

    // Read providers resource
    const providers = await client.readResource('providers://');

    console.log('Resource access completed');

  } catch (error) {
    console.error('Error in resource access:', error);
  } finally {
    await client.disconnect();
  }
}

async function exampleAIPrompts() {
  console.log('=== AI Prompts Example ===');

  const client = new HostFactoryMCPClient();

  try {
    await client.connect();

    // Get provision infrastructure prompt
    const provisionPrompt = await client.getPrompt('provision_infrastructure', {
      template_type: 'ec2',
      instance_count: 3
    });

    // Get troubleshooting prompt
    const troubleshootPrompt = await client.getPrompt('troubleshoot_deployment', {
      request_id: 'req-12345'
    });

    console.log('AI prompts example completed');

  } catch (error) {
    console.error('Error in AI prompts:', error);
  } finally {
    await client.disconnect();
  }
}

async function exampleErrorHandling() {
  console.log('=== Error Handling Example ===');

  const client = new HostFactoryMCPClient();

  try {
    await client.connect();

    // Try to call non-existent tool
    try {
      await client.callTool('non_existent_tool');
    } catch (error) {
      console.log('Expected error for non-existent tool:', error.message);
    }

    // Try to read non-existent resource
    try {
      await client.readResource('invalid://');
    } catch (error) {
      console.log('Expected error for invalid resource:', error.message);
    }

    console.log('Error handling example completed');

  } catch (error) {
    console.error('Error in error handling example:', error);
  } finally {
    await client.disconnect();
  }
}

async function main() {
  console.log('Starting Open Host Factory MCP Client Examples');
  console.log('================================================');

  const examples = [
    exampleBasicOperations,
    exampleInfrastructureProvisioning,
    exampleResourceAccess,
    exampleAIPrompts,
    exampleErrorHandling
  ];

  for (const example of examples) {
    try {
      await example();
      console.log();
    } catch (error) {
      console.error(`Error in ${example.name}:`, error);
      console.log();
    }
  }

  console.log('All examples completed');
}

// Run examples if this file is executed directly
if (require.main === module) {
  main().catch(console.error);
}

module.exports = { HostFactoryMCPClient };
