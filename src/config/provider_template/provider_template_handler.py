import json
import os
from typing import List, Dict, Any, Optional
from ..models.config.provider_template import ProviderTemplate
from src.helpers.logger import setup_logging

logger = setup_logging()


class ProviderTemplateManager:
    """
    Manages cloud provider templates loaded from a JSON file.
    Provides functionality for CRUD operations and validation of templates.
    """

    def __init__(self):
        """
        Initialize the ProviderTemplateManager and load templates from file.
        """
        self.templates: Dict[str, ProviderTemplate] = {}
        self.load_templates()

    def load_templates(self) -> None:
        """
        Load templates from the JSON file specified in the configuration.

        Raises:
            FileNotFoundError: If the template file does not exist.
            ValueError: If the template file contains invalid JSON or incorrect structure.
            json.JSONDecodeError: If the template file contains invalid JSON.
        """
        confdir = os.environ.get('HF_PROVIDER_CONFDIR', '/etc/hostfactory/providers/default')
        
        template_path = os.environ.get(
            'AWSPROV_TEMPLATES_PATH',
            os.path.join(confdir, 'awsprov_templates.json')
        )

        if not template_path or not isinstance(template_path, str):
            logger.error("Invalid or missing AWSPROV_TEMPLATES_PATH in configuration.")
            raise ValueError("Invalid or missing AWSPROV_TEMPLATES_PATH in configuration.")

        try:
            with open(template_path, 'r') as f:
                templates_data = json.load(f)
            
            # Ensure the top-level key is "templates" and it contains a list of dictionaries.
            if not isinstance(templates_data, dict) or 'templates' not in templates_data:
                logger.error(f"Invalid structure in template file {template_path}. Missing 'templates' key.")
                raise ValueError("The template file must contain a top-level 'templates' key with a list of dictionaries.")
            
            if not isinstance(templates_data['templates'], list):
                logger.error(f"Invalid structure in template file {template_path}. 'templates' key must contain a list.")
                raise ValueError("The 'templates' key must contain a list of dictionaries.")
            
            # Parse each template using ProviderTemplate model.
            self.templates.clear()
            for i, template in enumerate(templates_data['templates']):
                if not isinstance(template, dict) or 'templateId' not in template:
                    logger.error(f"Invalid template at index {i} in {template_path}. Missing 'templateId'.")
                    raise ValueError(f"Each template must be a dictionary with at least a 'templateId' key. Error at index {i}.")
                
                try:
                    self.templates[template['templateId']] = ProviderTemplate.from_dict(template)
                except Exception as e:
                    logger.error(f"Failed to parse template at index {i} in {template_path}. Error: {str(e)}")
                    raise ValueError(f"Failed to parse template at index {i}. Error: {str(e)}")
            
            logger.info(f"Successfully loaded {len(self.templates)} templates from {template_path}.")
        
        except FileNotFoundError:
            logger.error(f"Template file not found at path '{template_path}'.")
            raise FileNotFoundError(f"Template file not found at path '{template_path}'.")
        
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON format in template file '{template_path}'. Error details: {str(e)}")
            raise ValueError(f"Invalid JSON format in template file '{template_path}'. Error details: {str(e)}")

    def filter_templates(self, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Filter templates based on provided criteria.

        :param filters: A dictionary of filter criteria (e.g., {"templateId": "RunInstances", "awsHandler": "EC2Fleet"}).
                        If no filters are provided, return all templates.
        :return: A list of dictionaries representing matching templates.
        """
        if not filters:
            logger.info("No filters provided. Returning all templates.")
            return [template.to_dict() for template in self.templates.values()]

        logger.info(f"Filtering templates with criteria: {filters}")
        
        filtered_templates = []
        for template in self.templates.values():
            match = all(
                getattr(template, key, None) == value for key, value in filters.items()
            )
            if match:
                filtered_templates.append(template.to_dict())

        if not filtered_templates:
            logger.warning("No templates matched the provided filters.")
        
        return filtered_templates

    def get_template(self, template_id: str) -> ProviderTemplate:
        """
        Retrieve a specific template by its ID.

        :param template_id: The ID of the template to retrieve.
        :return: The corresponding ProviderTemplate object.
        
        Raises:
            ValueError: If no matching template exists.
        """
        if template_id not in self.templates:
            logger.error(f"Template '{template_id}' not found.")
            raise ValueError(f"Template '{template_id}' not found.")
        
        return self.templates[template_id]

    def get_all_templates(self, template_ids: Optional[List[str]] = None) -> List[ProviderTemplate]:
        """
        Retrieve all templates or specific templates based on a list of IDs.

        :param template_ids: A list of IDs to filter by. If None, return all templates.
        :return: A list of ProviderTemplate objects.

        :raises ValueError: If any provided ID does not match an existing template.
        """
        if template_ids:
            logger.info(f"Retrieving templates with IDs: {', '.join(template_ids)}")
            
            missing_ids = [tid for tid in template_ids if tid not in self.templates]
            if missing_ids:
                logger.error(f"The following templates were not found: {', '.join(missing_ids)}")
                raise ValueError(f"The following templates were not found: {', '.join(missing_ids)}")
            
            return [self.templates[tid] for tid in template_ids]

        # Return all templates if no specific IDs are provided
        logger.info("Retrieving all available templates...")
        return list(self.templates.values())

    def add_template(self, template: ProviderTemplate) -> None:
        """
        Add a new template to the manager.

        :param template: The ProviderTemplate to add.
        
        Raises:
            ValueError: If a template with the same ID already exists.
        """
        if template.templateId in self.templates:
            logger.error(f"Template '{template.templateId}' already exists.")
            raise ValueError(f"Template '{template.templateId}' already exists.")
        
        self.templates[template.templateId] = template
        logger.info(f"Added new template with ID '{template.templateId}'.")

    def update_template(self, template: ProviderTemplate) -> None:
        """
        Update an existing template.

        :param template: The updated ProviderTemplate object.
        
        Raises:
            ValueError: If the template does not exist.
        """
        if template.templateId not in self.templates:
            logger.error(f"Template '{template.templateId}' not found.")
            raise ValueError(f"Template '{template.templateId}' not found.")
        
        self.templates[template.templateId] = template
        logger.info(f"Updated template with ID '{template.templateId}'.")

    def delete_template(self, template_id: str) -> None:
        """
        Delete a specific template by its ID.

        :param template_id: The ID of the template to delete.
        
        Raises:
            ValueError: If the template does not exist.
        """
        if template_id not in self.templates:
            logger.error(f"Template '{template_id}' not found.")
            raise ValueError(f"Template '{template_id}' not found.")
        
        del self.templates[template_id]
        logger.info(f"Deleted template with ID '{template_id}'.")

    def list_templates(self) -> List[str]:
        """
        Get a list of all available template IDs.

        :return: A list of template IDs.
        """
        return list(self.templates.keys())

    def save_templates(self) -> None:
        """
        Save all templates to the JSON file specified in the configuration.

        Raises:
            FileNotFoundError: If the configuration path is invalid or inaccessible.
            IOError: If there is an error writing to the file.
        """
        confdir = os.environ.get('HF_PROVIDER_CONFDIR', '/etc/hostfactory/providers/default')
        
        # Use AWSPROV_TEMPLATES_PATH environment variable or default path
        template_path = os.environ.get(
            'AWSPROV_TEMPLATES_PATH',
            os.path.join(confdir, 'awsprov_templates.json')
        )

        if not isinstance(template_path, str):
            logger.error("Invalid or missing AWSPROV_TEMPLATES_PATH in configuration.")
            raise ValueError("Invalid or missing AWSPROV_TEMPLATES_PATH in configuration.")

        try:
            with open(template_path, 'w') as f:
                templates_data = [template.to_dict() for template in self.templates.values()]
                json.dump({"templates": templates_data}, f, indent=2)
                
                logger.info(f"Templates successfully saved to {template_path}.")
                
        except IOError as e:
            logger.error(f"Failed to save templates to {template_path}. Error: {str(e)}")
            raise IOError(f"Failed to save templates to {template_path}. Error: {str(e)}")

    def merge_templates(self, base_id: str, override_id: str) -> ProviderTemplate:
        """
        Merge two templates, with the override template taking precedence.

         Raises:
             ValueError: If either base or override templates are missing.
         """
        base_template = self.get_template(base_id)
        override_template = self.get_template(override_id)
        return base_template.merge(override_template)

    def validate_all_templates(self) -> List[str]:
         invalid_templates=[]

    def validate_all_templates(self) -> List[str]:
        """
        Validate all templates and return a list of invalid template IDs.

        :return: A list of template IDs that failed validation.
        """
        invalid_templates = []
        for template_id, template in self.templates.items():
            try:
                template.validate()
            except ValueError as e:
                logger.warning(f"Template '{template_id}' failed validation. Error: {str(e)}")
                invalid_templates.append(template_id)
        
        if invalid_templates:
            logger.warning(f"Found {len(invalid_templates)} invalid templates: {', '.join(invalid_templates)}")
        else:
            logger.info("All templates passed validation.")
        
        return invalid_templates

    def __str__(self) -> str:
        return f"ProviderTemplateManager(templates={len(self.templates)})"

    def __repr__(self) -> str:
        return self.__str__()
