-- 0002: what a factor classifies as, alongside what it measures
--
-- 0001 recorded where a factor came from. It did not record what the factor IS, and three
-- properties were carried as assumptions in code instead:
--
--   scope      `tools/calculate.py` had METHOD = 'location-based' as a module constant.
--              That is a Scope 2 term. Applied to a fuel-combustion factor it is simply
--              wrong, and the UI prints it. A figure's scope must come from the factor
--              row, not from a constant, the model, or a page caption.
--
--   method     Location-based is one of several; combustion is not a variant of it.
--
--   gas_basis  EMA's grid factor is CO2 only - its unit string says so ('kg CO2 / kWh').
--              Every DESNZ fuel row is CO2e, including CH4 and N2O. A total that adds a
--              CO2e line to a CO2 line is mixing bases, and would understate the CO2-only
--              line by its methane and nitrous oxide content without saying so. Recording
--              the basis per row is what lets the result disclose the mix.
--
-- Nullable rather than NOT NULL: adding a not-null column to a populated table needs a
-- default, and a DEFAULT here would be a claim about rows this migration has not read.
-- The backfill below sets the rows that exist; the loaders set them explicitly from then
-- on, and a row with a null scope is a loader defect, not a schema concession.
--
-- Idempotent: safe to re-run.

alter table emission_factors add column if not exists scope     smallint;
alter table emission_factors add column if not exists method    text;
alter table emission_factors add column if not exists gas_basis text;

alter table emission_factors drop constraint if exists emission_factors_scope_check;
alter table emission_factors add  constraint emission_factors_scope_check
    check (scope is null or scope in (1, 2, 3));

alter table emission_factors drop constraint if exists emission_factors_gas_basis_check;
alter table emission_factors add  constraint emission_factors_gas_basis_check
    check (gas_basis is null or gas_basis in ('CO2', 'CO2e'));

-- Backfill the rows loaded before this migration existed: EMA's grid emission factor is
-- location-based Scope 2, and CO2 only. Scoped by category and publisher rather than
-- applied to every null row, so a later loader that forgets to set these is not silently
-- labelled as EMA's.
update emission_factors
   set scope     = coalesce(scope, 2),
       method    = coalesce(method, 'location-based'),
       gas_basis = coalesce(gas_basis, 'CO2')
 where category = 'electricity_grid_sg'
   and publisher = 'EMA';

comment on column emission_factors.scope is
    'GHG Protocol scope the factor produces emissions in: 1, 2 or 3.';
comment on column emission_factors.method is
    'Accounting method, e.g. location-based (Scope 2 electricity) or combustion (Scope 1 fuel).';
comment on column emission_factors.gas_basis is
    'CO2 = carbon dioxide only; CO2e = all greenhouse gases as CO2 equivalent. A result '
    'combining both must disclose the mix.';
