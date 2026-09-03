import { index, integer, sqliteTable, text } from "drizzle-orm/sqlite-core";

// Admission timestamps and keyed network identifiers only; never tokens,
// raw IP addresses, repository URLs, source code or analysis reports.
export const deepAdmissions = sqliteTable("deep_admissions", {
  id: text("id").primaryKey(),
  clientKey: text("client_key").notNull(),
  createdAt: integer("created_at").notNull(),
}, (table) => [
  index("idx_deep_admissions_created_at").on(table.createdAt),
  index("idx_deep_admissions_client_created_at").on(table.clientKey, table.createdAt),
]);
