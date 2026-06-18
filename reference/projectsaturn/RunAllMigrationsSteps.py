import sys
import logging
import asyncio
import os
from dotenv import load_dotenv
from utils.log_utils import setup_logging
from FetchFromSharePoint import main as fetch_from_sharepoint_main
from CreateUsers import main as create_users_main
from CreateIdTags import main as create_id_tags_main
from CreateOrUpdatePartners import main as create_or_update_partners_main
from CreateOrUpdatePartnerContract import main as create_or_update_partner_contract_main
from CreateOrUpdateLocation import main as create_or_update_location_main
from CreateOrUpdateChargingZone import main as create_or_update_charging_zone_main
from CreateOrUpdateTariffGroupAndBaseTariff import main as create_or_update_tariff_group_and_base_tariff_main
from CreateOrUpdateChargePoints import main as create_or_update_charge_points_main
from CreateOrUpdateElectricityMeters import main as create_or_update_electricity_meters_main
from CreateOrUpdateCircuits import main as create_or_update_circuits_main
from CalculatePhysicalReferenceWithAI import main as calculate_physical_reference_with_ai_main
from CreateAndUpdateEvseAndConnector import main as create_and_update_evse_and_connector_main
from AttachChargePointToCircuit import main as attach_charge_point_to_circuit_main
from CreatePartnerInvites import main as create_partner_invites_main
from CreatePartnerAdmins import main as create_partner_admins_main
from CreateOrUpdateUserGroups import main as create_or_update_user_groups_main
from CreateOrUpdateSubscriptionPlan import main as create_or_update_subscription_plan_main
from CreateOrUpdateSiteTrackerSites import main as create_or_update_sitetracker_sites_main
from CreateOrUpdateSiteTrackerAccounts import main as create_or_update_sitetracker_accounts_main
from CreateOrUpdateSiteTrackerSiteRelations import main as create_or_update_sitetracker_site_relations_main
from CreateOrUpdateSiteTrackerFieldAssets import main as create_or_update_sitetracker_field_assets_main

load_dotenv()

async def main():
    sharepoint_file_name = os.getenv('sharepoint_file_name')
    if not sharepoint_file_name:
        logging.error("Missing 'sharepoint_file_name' in .env file. Exiting.")
        sys.exit(1)

    logging.info(f"Starting {sharepoint_file_name} import from Sharepoint")
    await fetch_from_sharepoint_main(sharepoint_file_name)

    # logging.info("Creating users...")
    # create_users_main()

    # logging.info("Creating ID tags...")
    # create_id_tags_main()

    # logging.info("Creating or updating partners...")
    # create_or_update_partners_main()

    # logging.info("Creating or updating partner contracts...")
    # create_or_update_partner_contract_main()

    # logging.info("Creating or updating locations...")
    # create_or_update_location_main()

    # logging.info("Creating or updating charging zones...")
    # create_or_update_charging_zone_main()

    # logging.info("Creating or updating user groups...")
    # create_or_update_user_groups_main()

    # logging.info("Creating or updating subscriptions...")
    # create_or_update_subscription_plan_main()

    # logging.info("Creating or updating tariff groups and base tariffs...")
    # create_or_update_tariff_group_and_base_tariff_main()

    # logging.info("Creating or updating charge points...")
    # create_or_update_charge_points_main()

    # logging.info("Creating or updating electricity meters...")
    # create_or_update_electricity_meters_main()

    # logging.info("Creating or updating circuits...")
    # create_or_update_circuits_main()

    # logging.info("Calculating PhysicalReference values with AI...")
    # calculate_physical_reference_with_ai_main()

    # logging.info("Creating and updating EVSE and connectors...")
    # create_and_update_evse_and_connector_main()

    # logging.info("Attaching charge points to circuits...")
    # attach_charge_point_to_circuit_main()

    # logging.info("Creating partner invites...")
    # create_partner_invites_main()

    # logging.info("Creating partner admins...")
    # create_partner_admins_main()

    logging.info("Creating or updating SiteTracker sites...")
    create_or_update_sitetracker_sites_main()

    logging.info("Creating or updating SiteTracker accounts...")
    create_or_update_sitetracker_accounts_main()

    logging.info("Creating or updating SiteTracker site relations...")
    create_or_update_sitetracker_site_relations_main()

    logging.info("Creating or updating SiteTracker field assets...")
    create_or_update_sitetracker_field_assets_main()

if __name__ == "__main__":
    setup_logging("RunAllMigrationsSteps")
    asyncio.run(main())