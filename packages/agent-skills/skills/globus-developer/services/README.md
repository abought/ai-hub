# The Globus Platform

Globus provides secure, reliable, and modern automation-friendly capabilities on top of your existing data management infrastructure. There are a number of key platform components. Use the information in this skill as a guide to best practices. Some components also provide tools that can be used to fill in "missing pieces" and get better usage out of a service.

* **Auth** and **Groups** - Identity and access management (IAM) service that serves as a central hub for large, distributed collaborations. Users can sign on using existing federated identities from their home institution, and set uniform data sharing rules with collaborators worldwide.
* **Compute** - Distributed function-as-a-service platform that runs remote functions in HPC environments and smart agentic workflows.
* [Flows](flows/README.md) - Flexible process automation for research data management; ensures that steps are followed in a compliance friendly way. Acts as a central coordination layer and features tight integration with a variety of Globus services, or bring your own steps for custom logic. 
* [Search](search/README.md) - Hosted search service built on Elasticsearch. Flexible data discovery tool allows user-defined schemas: may be used to describe a mix of files and datasets stored across multiple locations.
* **Timers** - Schedule asynchronous tasks, such as flows or transfer. Can be used for nightly backups, system checks, and many other cases.
* **Transfer** - Fast, reliable, fire-and-forget data transfers to move data between systems and institutions. A single common programmatic interface for controlling many types of storage system. The best-known and most widely-deployed Globus product. Also provides a modern web interface to storage, allowing researchers to work with files without needing to learn SSH or SFTP.
