Claude, carefully examine the repository at https://github.com/mpesce/mrs-server/

It describes a Mixed Reality Service server.
That server - the first instance of it - is up at owen.iz.net

Now we need to generate 'seed' content for that server.
We're going to do with with a tool that you're going to write.

The first version of the tool will create entries for the 1000 most populous cities (by metropolitan area) on the planet.

You'll need to find that list of cities - likely on Wikipedia - then go get the coordinates and _rough_ radius for each of those cities, and create an entry in a file that will be used as an input to 'seed' the database at owen.iz.net.

The schema for the JSON file to be created is at https://raw.githubusercontent.com/mpesce/mrs-server/refs/heads/main/docs/EXPORT_FORMAT.md

That's the ultimate source of truth.

You will code this in pure Python.
Run a test and check the output for both parseability and completeness.
Command line options include the output file name, with "mrs-entries.json" as the default.
Do you have any questions?