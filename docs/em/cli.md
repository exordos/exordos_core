---
icon: lucide/terminal
---
# Examples

## Build element from repository

 Build a Exordos element. The command build all images, manifests and other artifacts required for the element. The manifest in the project may be a raw YAML file or a template using Jinja2 templates. For Jinja2 templates, the following variables are
 available by default:

- {{ version }}: version of the element
- {{ name }}: name of the element
- {{ images }}: list of images
- {{ manifests }}: list of manifests

Go to project directory and run the following command:

```bash
exordos build <element_name>
```

## Install element from repository

```bash
exordos elements install <element_name>
```
