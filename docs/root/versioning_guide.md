#  Documentation Versioning Guide

## Overview

This project uses **Mike** for versioned documentation that aligns with software releases. Each version of the software has corresponding documentation, making it easy for users to access the right documentation for their version.

##  Why Versioned Documentation?

### **Professional Benefits:**
- **Release Alignment**: Documentation matches software versions
- **User Experience**: Users can access docs for their specific version
- **Historical Reference**: Previous versions remain accessible
- **API Compatibility**: Version-specific API documentation
- **Migration Guides**: Clear upgrade paths between versions

### **Development Benefits:**
- **Feature Documentation**: New features documented in appropriate versions
- **Breaking Changes**: Clear documentation of version-specific changes
- **Maintenance**: Easy to maintain docs for multiple active versions
- **Deployment**: Automated deployment with releases

##  Current Setup

### **Installed Tools:**
- **Mike**: Documentation versioning tool
- **MkDocs**: Documentation generator
- **Material Theme**: Professional documentation theme

### **Current Versions:**
```bash
# List all versions
make docs-list-versions
# Output: 0.1.0 [latest]
```

### **Version Structure:**
- **0.1.0**: Current development version (latest)
- **latest**: Alias pointing to most recent version
- **Future**: 0.2.0, 1.0.0, etc. as releases are made

##  Usage Guide

### **Local Development:**

#### **Serve Versioned Documentation:**
```bash
# Serve with version switcher (recommended)
make docs-serve

# Access at: http://127.0.0.1:8000
```

#### **Serve Development Documentation:**
```bash
# Serve without versioning (for active development)
make docs-serve-dev

# Access at: http://127.0.0.1:8000
```

### **Version Management:**

#### **List All Versions:**
```bash
make docs-list-versions
```

#### **Deploy New Version:**
```bash
# Deploy version 1.0.0 and make it latest
make docs-deploy-version VERSION=1.0.0

# Deploy version 0.2.0 but keep 1.0.0 as latest
mike deploy 0.2.0
```

#### **Delete Version:**
```bash
# Delete version 0.1.0
make docs-delete-version VERSION=0.1.0
```

### **Release Workflow:**

#### **When Creating a New Release:**

1. **Update Version in Code:**
   ```bash
   # Update src/__init__.py
   __version__ = "1.0.0"
   ```

2. **Deploy Documentation Version:**
   ```bash
   # Deploy and make it latest
   make docs-deploy-version VERSION=1.0.0
   ```

3. **Verify Deployment:**
   ```bash
   # Check versions
   make docs-list-versions
   # Should show: 1.0.0 [latest], 0.1.0
   ```

4. **Test Version Switcher:**
   ```bash
   # Serve locally and test version dropdown
   make docs-serve
   ```

##  Technical Details

### **Mike Configuration:**
```yaml
# docs/mkdocs.yml
extra:
  version:
    provider: mike
```

### **File Structure:**
```
docs/
+--- mkdocs.yml          # MkDocs configuration
+--- docs/               # Documentation source
+--- site/               # Built documentation (versioned)
```

### **Version Storage:**
- **Local**: `docs/site/` contains all versions
- **GitHub Pages**: `gh-pages` branch contains deployed versions
- **Version Index**: `versions.json` tracks all versions

### **URL Structure:**
```
https://your-org.github.io/open-hostfactory-plugin/
+--- /                   # Latest version (redirects)
+--- /0.1.0/            # Version 0.1.0
+--- /1.0.0/            # Version 1.0.0
+--- /latest/           # Latest version (alias)
```

##  User Experience

### **Version Switcher:**
- **Dropdown Menu**: Users can switch between versions
- **Latest Badge**: Clear indication of latest version
- **Version URLs**: Direct links to specific versions
- **Responsive Design**: Works on all devices

### **Navigation:**
- **Consistent Layout**: Same navigation across versions
- **Version-Specific Content**: Features documented in appropriate versions
- **Cross-Version Links**: Links to equivalent pages in other versions

##  Advanced Usage

### **Multiple Aliases:**
```bash
# Create stable and development aliases
mike deploy --update-aliases 1.0.0 stable
mike deploy --update-aliases 1.1.0-beta dev
```

### **Custom Titles:**
```bash
# Deploy with custom title
mike deploy --title "Version 1.0.0 (Stable)" 1.0.0 stable
```

### **Retitle Versions:**
```bash
# Change version title
mike retitle 1.0.0 "Version 1.0.0 (LTS)"
```

##  Best Practices

### **Version Naming:**
- **Semantic Versioning**: Use semver (1.0.0, 1.1.0, 2.0.0)
- **Consistent Format**: Always use same format
- **Clear Aliases**: Use meaningful aliases (stable, latest, dev)

### **Content Management:**
- **Version-Specific Features**: Document new features in appropriate versions
- **Breaking Changes**: Clearly mark breaking changes
- **Migration Guides**: Provide upgrade instructions
- **Deprecation Notices**: Mark deprecated features

### **Deployment Strategy:**
- **Automated Deployment**: Deploy docs with software releases
- **Testing**: Test version switcher before deployment
- **Cleanup**: Remove old versions when no longer supported
- **Backup**: Keep local copies of important versions

##  Integration with CI/CD

### **GitHub Actions Example:**
```yaml
# .github/workflows/docs.yml
name: Deploy Documentation
on:
  release:
    types: [published]

jobs:
  deploy-docs:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v4
    - name: Setup Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.11'

    - name: Install dependencies
      run: |
        pip install -r requirements-dev.txt

    - name: Deploy documentation
      run: |
        cd docs
        mike deploy --push --update-aliases ${{ github.event.release.tag_name }} latest
```

##  Benefits Achieved

### **For Users:**
- [[]] **Version-Specific Documentation**: Always accurate for their version
- [[]] **Easy Navigation**: Version switcher in documentation
- [[]] **Historical Access**: Can access older version docs
- [[]] **Professional Experience**: Clean, versioned documentation

### **For Developers:**
- [[]] **Release Alignment**: Docs automatically match releases
- [[]] **Easy Maintenance**: Simple commands for version management
- [[]] **Automated Deployment**: Integrate with release process
- [[]] **Quality Assurance**: Version-specific testing and validation

### **For Project:**
- [[]] **Professional Image**: Shows mature project management
- [[]] **User Support**: Reduces support burden with accurate docs
- [[]] **Adoption**: Easier for users to adopt and upgrade
- [[]] **Maintenance**: Easier to maintain multiple versions

##  Next Steps

1. **Test Version Switcher**: Verify dropdown works correctly
2. **Create 1.0.0 Release**: Deploy first stable version
3. **Automate Deployment**: Add to CI/CD pipeline
4. **Document Migration**: Create upgrade guides between versions
5. **Monitor Usage**: Track which versions are most accessed

**Your documentation is now professionally versioned and ready for production releases!** 
