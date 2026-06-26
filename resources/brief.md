Macgear will be providing a 3PL service to Mova, who are a supplier of robotic vaccums. This 3PL service includes receiving their stock and storing it in our warehouse and dispatching it for them. We are not buying the stock, do not own the stock we just transact it for them and charge them a fee for receiving, storing and dispatching it. It will be stored in a dedicated 3PL area of our Melbourne warehouse.

Netsuite setup
Set up a new location for purpose of receiving  and storing the stock  called '3PL warehouse'
Set up Mova as a customer record
To invoice them for the 3PL service charges
To key the sales orders for dispatching to their customers - orders will be keyed at $0 as we do not own the stock
Set up Mova as a supplier record
To create PO's and receive stock against
Set up the items in netsuite with the Brand being set as Mova 3PL, and quantity per pallet populated

Processes

Receiving the stock 
Mova to ship us 20,000 units from China
create PO at $0 on Mova supplier account (as we do not own the stock) against 3PL location
Create an inbound shipment per container
Receipt inbound shipment once it arrives 

Storage
Stock on hand will be items in 3PL location and brand is Mova 3PL
Storage is charged per pallet per week for these items

Dispatching
Sales order will be keyed on Mova customer record to dispatch the stock
Or 
In some cases it will be Macgear who need to buy the stock from Mova 3PL for our inventory in that case 
Create VRMA at $0 to  fulfil to remove from 3pl inventory and physically move the stock from 3PL area of the warehouse to Macgear warehouse
Create PO against Mova supplier at our normal buy price to our normal warehouse location (Melbourne) and receipt in as usual

Billing
We will create the following weekly scheduled saved searches to facilitate the billing and the invoices will be raised weekly against Mova customer record
Inbound shipments received = per container charge
Item receipts against 3PL warehouse for brand Mova 3PL, showing line detail and quantity = per unit putaway charge
Item on hand divided by quantity per pallet = per pallet storage charge 
Item fulfilments on Mova customer = per unit pick fee 
Item fulfilments from VRMA on mova supplier - per unit pick fee (for stock transferred to Macgear)


Mova require visibility on a portal of the following (we can create these saved searches)
Stock on order - open PO's
Item receipts
Stock on hand - on hand in 3pl warehouse for brand mova 3PL
Item fulfilments from Sales orders and VRMAs
Invoices of the 3PL service charges on Mova customer account
Their rate card, which will look something like the below table



container unload 40ft - unpalletised (loose stacked
 $                                                  1500
  stock is loose stacked and requires manual unloading from the container (covers labour, supplying pallets)
 
 
 
putaway fee
 $                                                     1.00
  per unit putaway fee, covers putaway, receipting, serialisation
 
 
 
storage
 $                                                     4.50
  per pallet per week
 
 
 
picking fee 
 $                                                     1.00
  per unit picking fee, covers internal stock transfer, system movement, serialisation
Shipping fee                                    $                            as per shipping rate card


Hope this all makes sense. The priority is to get the portal for the visibility for Mova but I would also be keen to look at automating the billing section above rather than just relying on saved searches and manual intervention.

We are already doing this for another customer in the NZ subsidiary on a tiny scale compared to the above requirement but it is set up in both production and sandbox that we could use for testing. Their name is Skriva and the item is S-STYCASE-WHITE, the main difference is that we transact it all at $0 on our main warehouse 'Auckland' rather than having a separate 3PL location for the stock. 

Give me a call if you need further info. I am in Melbourne next week in the thick of a warehouse relocation but can be available for a call. Then I am in Bali from 9th to 20th July. We expect to start receiving stock from Mova towards the end of July so keen to get something in place as soon as we can, even if it is just some basics which we can expand out over time.