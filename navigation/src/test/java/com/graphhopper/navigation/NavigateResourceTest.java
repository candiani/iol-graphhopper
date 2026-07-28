package com.graphhopper.navigation;

import com.graphhopper.GHRequest;
import org.junit.jupiter.api.Test;

import java.util.List;

import static com.graphhopper.util.Parameters.CH.DISABLE;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class NavigateResourceTest {

    @Test
    void headingDisablesCHForPostNavigationRequests() {
        GHRequest request = new GHRequest();
        request.setHeadings(List.of(90d));

        NavigateResource.prepareNavigationRequest(request);

        assertTrue(request.getHints().getBool(DISABLE, false));
    }

    @Test
    void requestWithoutHeadingKeepsCHEnabled() {
        GHRequest request = new GHRequest();

        NavigateResource.prepareNavigationRequest(request);

        assertFalse(request.getHints().getBool(DISABLE, false));
    }
}
