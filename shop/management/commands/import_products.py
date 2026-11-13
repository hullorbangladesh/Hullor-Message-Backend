from django.core.management.base import BaseCommand, CommandError

from shop.importer import import_products_csv


class Command(BaseCommand):
    help = "Import or update products from a CSV file (columns: name, price, sku, name_bn, category, keywords, description, stock, image_url)"

    def add_arguments(self, parser):
        parser.add_argument("csv_path")

    def handle(self, *args, **options):
        try:
            with open(options["csv_path"], "rb") as fh:
                data = fh.read()
        except OSError as exc:
            raise CommandError(str(exc))
        result = import_products_csv(data)
        self.stdout.write(self.style.SUCCESS(f"Created {result.created}, updated {result.updated}"))
        for err in result.errors:
            self.stderr.write(err)
