import logging
from src.config.provider_template_manager import ProviderTemplateManager
from src.helpers.logger import setup_logging

logger = setup_logging()


class GetAvailableTemplates:
    """
    API to retrieve available templates.
    """

    def __init__(self, template_manager: ProviderTemplateManager):
        """
        Initialize GetAvailableTemplates with a ProviderTemplateManager instance.

        :param template_manager: An instance of ProviderTemplateManager.
        """
        self.template_manager = template_manager

    def execute(self, input_data=None) -> dict:
        """
        Retrieve available templates. If filters are provided in input_data, apply those filters.

        :param input_data: Optional dictionary containing filter criteria (e.g., {"templateId": "RunInstances"}).
                           Example input_data format:
                           {
                               "templateId": "RunInstances",
                               "awsHandler": "EC2Fleet"
                           }
                           If no input_data is provided, return all available templates.
                           
        :return: A dictionary containing filtered or all available templates and a success message.
                 Example output format:
                 {
                     "templates": [
                         {
                             "templateId": "RunInstances",
                             ...
                         }
                     ],
                     "message": "Get available templates success."
                 }
                 If no matching templates are found, an empty list is returned with an appropriate message.
        """
        try:
            logger.info("Retrieving available templates...")

            # Use input_data as filters for retrieving templates
            filters = input_data if input_data else {}
            
            # Retrieve filtered or all templates using ProviderTemplateManager
            templates = self.template_manager.filter_templates(filters=filters)

            return {
                "templates": templates,
                "message": f"Get available templates success. Retrieved {len(templates)} matching templates."
            }

        except Exception as e:
            logger.error(f"Error retrieving templates: {e}", exc_info=True)
            return {
                "error": str(e),
                "message": "Failed to retrieve available templates."
            }
