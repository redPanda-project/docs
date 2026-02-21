# 📄 Redpanda Documentation

Architecture documentation and milestone specifications for the **Redpanda** peer-to-peer messaging network.

Redpanda provides the "WhatsApp feeling" for true peer-to-peer communication — reliable enough for everyday use, private enough for high-risk requirements, lightweight enough for mobile devices, and trustless by design.

## Related Repositories

| Repository | Description | Language |
|------------|-------------|----------|
| [redpandaj](https://github.com/redPanda-project/redpandaj) | Full Node / Backend — DHT, Garlic routing, Outbound Handle service | Java |
| [redpanda-mobile](https://github.com/redPanda-project/redpanda-mobile) | Light Client / Mobile App — Flutter-based chat application | Dart / Flutter |

## Repository Structure

```
docs/
├── index.adoc                          # Main entry point (includes all arc42 sections)
├── 01_introduction_and_goals.adoc      # Vision, core goals, stakeholders
├── 02_architecture_constraints.adoc    # Technical constraints & differentiation
├── 03_system_scope_and_context.adoc    # Business & technical context
├── 04_solution_strategy.adoc           # Garlic Routing, Reverse Garlic, ACK system
├── 05_building_block_view.adoc         # System decomposition (Light Client, Full Node, DHT)
├── 06_runtime_view.adoc               # Runtime scenarios
├── 07_deployment_view.adoc            # Deployment topology
├── 08_concepts.adoc                   # Cross-cutting concepts
├── 09_architectural_decisions.adoc    # ADRs
├── 10_quality_requirements.adoc       # Quality tree & scenarios
├── 11_risks_and_technical_debt.adoc   # Known risks
├── 12_glossary.adoc                   # Terminology (Channel, DHT, Garlic, OH, …)
├── milestones/                        # Milestone specifications & status
│   ├── 00_status_overview.md          # Status matrix (Backend → Frontend)
│   ├── backend/                       # Backend-specific milestones
│   ├── frontend/                      # Frontend-specific milestones
│   └── ms01-ms09 *.md                 # Full-stack milestone specs
└── arc42_archive/                     # Archived early design notes
```

The architecture documentation follows the [arc42](https://arc42.org) template.

## Generating an HTML Site

The architecture docs are written in [AsciiDoc](https://asciidoc.org/). You can generate a self-contained HTML page with [Asciidoctor](https://asciidoctor.org/):

### Using Asciidoctor (Ruby)

```bash
# Install Asciidoctor
gem install asciidoctor

# Generate HTML
asciidoctor docs/index.adoc -o build/index.html
```

### Using Asciidoctor.js (Node.js)

```bash
# Install via npm
npm install -g @asciidoctor/cli

# Generate HTML
npx asciidoctor docs/index.adoc -o build/index.html
```

### Using Docker (no local install)

```bash
docker run --rm -v $(pwd):/documents asciidoctor/docker-asciidoctor \
  asciidoctor docs/index.adoc -o build/index.html
```

Open `build/index.html` in your browser to view the full architecture document with a table of contents and syntax highlighting.

## License

See the individual files for licensing information.
