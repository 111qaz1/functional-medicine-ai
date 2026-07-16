import { ProductCatalogManager } from "../../components/product-catalog-manager";
import { AdminGate } from "../../components/admin-gate";

export default function ProductCatalogPage() {
  return <AdminGate><ProductCatalogManager /></AdminGate>;
}
