#include <pciaccess.h>

#include <stdio.h>

int main(void) {
    struct pci_slot_match match = {
        PCI_MATCH_ANY,
        PCI_MATCH_ANY,
        PCI_MATCH_ANY,
        PCI_MATCH_ANY,
        0,
    };
    struct pci_device_iterator *iter = pci_slot_match_iterator_create(&match);
    if (iter != NULL) {
        fprintf(stderr, "iterator unexpectedly allocated before pci_system_init\n");
        pci_iterator_destroy(iter);
        return 1;
    }

    if (pci_device_next(NULL) != NULL) {
        fprintf(stderr, "pci_device_next(NULL) returned a device\n");
        return 2;
    }

    pci_iterator_destroy(NULL);
    pci_system_cleanup();

    printf("libpciaccess:0.17:null-iterator-ok\n");
    return 0;
}
