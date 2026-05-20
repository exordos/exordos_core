# OpenAPI Specification

You can view the OpenAPI specification for the Orch API here:

<div id="swagger-ui"></div>

<script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
<style>
  /* Dark theme support for Swagger UI */
  [data-md-color-scheme="slate"] #swagger-ui {
    filter: invert(1) hue-rotate(180deg);
  }
  [data-md-color-scheme="slate"] #swagger-ui .microlight {
    filter: invert(1) hue-rotate(180deg);
  }
  [data-md-color-scheme="slate"] #swagger-ui .swagger-ui svg {
    filter: invert(1) hue-rotate(180deg);
  }
</style>

<script src="https://unpkg.com/js-yaml@4/dist/js-yaml.min.js"></script>
<script>
  fetch('../openapi_orch.yaml')
    .then(response => response.text())
    .then(yamlText => {
      const spec = jsyaml.load(yamlText);
      SwaggerUIBundle({
        spec: spec,
        dom_id: '#swagger-ui',
        presets: [SwaggerUIBundle.presets.apis],
        validatorUrl: null,
        tryItOutEnabled: true
      });
    })
    .catch(err => {
      console.error('Failed to load spec:', err);
      document.getElementById('swagger-ui').innerHTML = '<p>Error loading API spec: ' + err.message + '</p>';
    });
</script>
